""" 
Conversation helpers for preparing LLM context and (optionally) summarizing old history.

Improvements vs previous version:
 - Strong defensive coding around DB calls (no silent failures)
 - Better separation of concerns (context builder vs summarizer)
 - Stable trimming pipeline aligned with llm_utils
 - Clean batch streaming helpers for very large conversations
 - No API changes. All function signatures preserved exactly.
"""

from typing import List, Tuple, Optional, Iterable
import logging

from app.llm_utils import trim_messages_for_context
from app.llm_client_offload import generate_response

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------
# Small helpers
# -------------------------------------------------------------------

def _to_llm_message(role: str, content: str) -> dict:
    """
    Normalize a DB message row into an LLM-friendly dict.
    """
    return {
        "role": role,
        "content": content or ""
    }


# -------------------------------------------------------------------
# CONTEXT BUILDER
# -------------------------------------------------------------------

def build_context_for_model(
    db,
    conv_id: str,
    *,
    max_tokens_chars: int = 6000,
    recent_limit: int = 20,
    include_summary: bool = True,
) -> List[dict]:
    """
    Build a compact trimmed list of messages for the model.

    Strategy:
      1) Get Conversation.summary and convert into a system message.
      2) Load last `recent_limit` messages ordered newest-last.
      3) Prepend summary and trim using llm_utils.trim_messages_for_context.
    """
    try:
        from app.db.crud_conversation import get_conversation, get_recent_messages
    except Exception as e:
        logger.exception("build_context_for_model: cannot import DB CRUD: %s", e)
        return [{"role": "system", "content": ""}]

    # ----------------------------------------------------------
    # 1) load conversation + summary
    # ----------------------------------------------------------
    summary_msg: List[dict] = []
    try:
        conv = get_conversation(db, conv_id)
        if include_summary and conv and getattr(conv, "summary", None):
            summary_msg.append(
                _to_llm_message("system", f"Conversation summary: {conv.summary}")
            )
    except Exception as e:
        logger.exception("build_context: failed to load conversation summary: %s", e)

    # ----------------------------------------------------------
    # 2) load recent messages
    # ----------------------------------------------------------
    msgs: List[dict] = []
    try:
        recent = get_recent_messages(db, conv_id, limit=recent_limit) or []
        for m in recent:
            # Works for SQLAlchemy row or dict
            role = getattr(m, "role", None) or m.get("role", "user")
            content = getattr(m, "content", None) or m.get("content", "")
            msgs.append(_to_llm_message(role, content))
    except Exception as e:
        logger.exception("build_context: failed to load recent messages: %s", e)

    # ----------------------------------------------------------
    # 3) final trim
    # ----------------------------------------------------------
    combined = summary_msg + msgs
    trimmed, _ = trim_messages_for_context(combined, "", max_total_chars=max_tokens_chars)

    return trimmed


# -------------------------------------------------------------------
# SUMMARIZER
# -------------------------------------------------------------------

def maybe_summarize_old_history(
    db,
    conv_id: str,
    *,
    threshold_messages: int = 200,
    batch_size: int = 200,
    summarization_max_tokens: int = 200,
    delete_after_summarize: bool = True,
) -> Optional[str]:
    """
    Summarize older messages beyond a threshold.

    Steps:
      - Count messages.
      - If > threshold, load oldest chunks in batches.
      - Concatenate + send to LLM summarizer.
      - Persist new summary via crud_summary.update_summary.
      - Optionally delete older messages.
    """
    try:
        from app.db.crud_conversation import (
            get_conversation,
            count_messages_for_conversation,
            fetch_messages_older_than,
        )
    except Exception as e:
        logger.exception("maybe_summarize: missing CRUD functions: %s", e)
        return None

    # ----------------------------------------------------------
    # check total size
    # ----------------------------------------------------------
    try:
        total = count_messages_for_conversation(db, conv_id)
    except Exception:
        logger.exception("maybe_summarize: cannot count messages")
        total = 0

    if total <= threshold_messages:
        return None

    # ----------------------------------------------------------
    # load old messages in batches
    # ----------------------------------------------------------
    older: List[Tuple[str, str]] = []
    try:
        offset = 0
        while True:
            batch = fetch_messages_older_than(
                db,
                conv_id,
                keep_recent=threshold_messages,
                limit=batch_size,
                offset=offset,
            )
            if not batch:
                break

            for m in batch:
                role = getattr(m, "role", None) or m.get("role", "user")
                content = getattr(m, "content", None) or m.get("content", "")
                older.append((role, content))

            offset += len(batch)
            if len(batch) < batch_size:
                break

    except Exception:
        logger.exception("maybe_summarize: fetch_messages_older_than failed")
        # fallback
        try:
            conv = get_conversation(db, conv_id)
            all_msgs = getattr(conv, "messages", None)
            if all_msgs:
                old_slice = all_msgs[:-threshold_messages]
                older = [(m.role, m.content) for m in old_slice]
        except Exception:
            logger.exception("maybe_summarize: fallback failed")

    if not older:
        return None

    # ----------------------------------------------------------
    # prepare prompt
    # ----------------------------------------------------------
    text = "\n".join(f"{r}: {c}" for r, c in older)
    prompt = [
        {
            "role": "system",
            "content": (
                "You are a succinct summarizer. Produce a short summary "
                "(1–3 sentences) suitable for conversation context."
            ),
        },
        {"role": "user", "content": text},
    ]

    # ----------------------------------------------------------
    # call summarizer LLM
    # ----------------------------------------------------------
    try:
        summary = generate_response(prompt, max_new_tokens=summarization_max_tokens)
    except Exception as e:
        logger.exception("maybe_summarize: summarizer LLM failed: %s", e)
        return None

    if not summary:
        return None

    # ----------------------------------------------------------
    # persist summary
    # ----------------------------------------------------------
    try:
        from app.db.crud_summary import update_summary
        from app.db.crud_conversation import delete_old_messages
    except Exception:
        update_summary = None
        delete_old_messages = None

    try:
        # merge with existing summary
        try:
            conv = get_conversation(db, conv_id)
            existing = getattr(conv, "summary", "") or ""
        except Exception:
            existing = ""

        new_summary = (existing + " " + summary).strip()

        if update_summary:
            update_summary(db, conv_id, new_summary)
        else:
            logger.warning("maybe_summarize: update_summary not available")

        if delete_after_summarize and delete_old_messages:
            delete_old_messages(db, conv_id, keep_recent=threshold_messages)
    except Exception:
        logger.exception("maybe_summarize: failed to update summary / delete messages")

    return summary


# -------------------------------------------------------------------
# BATCH STREAMING (OPTIONAL UTILITY)
# -------------------------------------------------------------------

def iter_messages_in_batches(
    db,
    conv_id: str,
    batch_size: int = 500
) -> Iterable[Tuple[str, str]]:
    """
    Yield all messages in ascending order in batches.
    """
    try:
        from app.db.crud_conversation import fetch_all_messages_in_batches
    except Exception:
        logger.debug("iter_messages_in_batches: missing helper")
        return
        yield  # ensure generator

    offset = 0
    while True:
        try:
            batch = fetch_all_messages_in_batches(
                db, conv_id, limit=batch_size, offset=offset
            )
        except Exception:
            logger.exception("iter_messages_in_batches: failed to fetch")
            return

        if not batch:
            return

        for m in batch:
            role = getattr(m, "role", None) or m.get("role", "user")
            content = getattr(m, "content", None) or m.get("content", "")
            yield (role, content)

        offset += len(batch)
