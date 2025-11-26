"""
Lightweight JSON-backed conversation memory.

- Keeps a dict[str, Conversation] in memory.
- Persists conversations to disk (CONV_FILE) in a crash-safe way.
- Provides helpers to get/create conversations and update history + summary.

This is a *fallback* store: the DB layer can coexist and/or replace this,
but higher-level code still expects these functions and globals to exist.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from typing import Dict, List, TypedDict

from app.config import (
    CONV_FILE,
    DATA_DIR,
    MAX_HISTORY_TURNS,
    MAX_SUMMARY_CHARS,
)
from app.llm_client_offload import generate_response

# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# TYPE DEFINITIONS
# ---------------------------------------------------------------------
class Message(TypedDict):
    role: str
    content: str


class Conversation(TypedDict):
    summary: str
    messages: List[Message]


# ---------------------------------------------------------------------
# GLOBALS
# ---------------------------------------------------------------------
_lock = threading.Lock()
conversations: Dict[str, Conversation] = {}


# ---------------------------------------------------------------------
# FILE UTILITIES
# ---------------------------------------------------------------------
def _safe_json_load(path: str) -> Dict[str, Conversation]:
    """
    Load JSON from disk safely.
    If file is missing or corrupted, return {} instead of crashing.

    The on-disk format is expected to be:
      { "<conv_id>": {"summary": str, "messages": [{"role": str, "content": str}, ...]}, ... }
    """
    if not os.path.exists(path):
        logger.debug("Memory file not found at %s; starting with empty store.", path)
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        # corrupted file, invalid JSON, partial write, etc.
        logger.warning("Failed to load memory file %s: %s; using empty store.", path, exc)
        return {}

    if not isinstance(data, dict):
        logger.warning(
            "Memory file %s did not contain a dict root; ignoring and using empty store.",
            path,
        )
        return {}

    # Best-effort shape check; we don't enforce strictly to avoid breaking old data.
    result: Dict[str, Conversation] = {}
    for conv_id, conv in data.items():
        if not isinstance(conv_id, str) or not isinstance(conv, dict):
            continue
        summary = conv.get("summary", "") if isinstance(conv.get("summary", ""), str) else ""
        msgs_raw = conv.get("messages") or []
        msgs: List[Message] = []
        if isinstance(msgs_raw, list):
            for m in msgs_raw:
                if not isinstance(m, dict):
                    continue
                role = str(m.get("role", "user"))
                content = str(m.get("content", ""))
                msgs.append({"role": role, "content": content})
        result[conv_id] = {"summary": summary, "messages": msgs}

    return result


def _safe_json_write(path: str, data: Dict[str, Conversation]) -> None:
    """
    Atomic write: write to a temp file then replace.
    Prevents corruption if process crashes during write.
    """
    os.makedirs(DATA_DIR, exist_ok=True)

    tmp_file = f"{path}.tmp"
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, path)
    except Exception as exc:
        logger.exception("Failed to persist memory file %s: %s", path, exc)
        # Best-effort: tmp file may remain; not fatal for runtime behavior.


# ---------------------------------------------------------------------
# PUBLIC FILE API
# ---------------------------------------------------------------------
def load_conversations() -> Dict[str, Conversation]:
    """
    Load all conversations from disk into memory.
    Thread-safe wrapper over _safe_json_load.
    """
    with _lock:
        return _safe_json_load(CONV_FILE)


def save_conversations(data: Dict[str, Conversation]) -> None:
    """
    Persist given conversations dict to disk.
    Thread-safe wrapper over _safe_json_write.
    """
    with _lock:
        _safe_json_write(CONV_FILE, data)


# Initialize in-memory store
conversations = load_conversations() or {}


# ---------------------------------------------------------------------
# MAIN MEMORY API
# ---------------------------------------------------------------------
def get_or_create_conversation(conversation_id: str | None = None) -> str:
    """
    Return an existing conversation id or create a new one.

    - If `conversation_id` is provided and exists, it is returned.
    - Otherwise, a new UUID is generated, stored, and returned.
    """
    global conversations

    with _lock:
        if conversation_id and conversation_id in conversations:
            return conversation_id

        new_id = str(uuid.uuid4())
        conversations[new_id] = {"summary": "", "messages": []}
        save_conversations(conversations)
        logger.debug("Created new in-memory conversation %s", new_id)
        return new_id


def summarize_old_messages(old_messages: List[Message]) -> str:
    """
    Convert a set of messages into a compact summary using the LLM.
    Fail quietly if LLM is unavailable or fails.

    NOTE: This function is not currently wired into update_memory,
    but kept for compatibility and possible future use.
    """
    if not old_messages:
        return ""

    text = "\n".join(f"{m['role']}: {m['content']}" for m in old_messages)

    try:
        return generate_response(
            [
                {"role": "system", "content": "Summarize the following dialog for memory."},
                {"role": "user", "content": text},
            ],
            max_new_tokens=200,
        )
    except Exception as exc:
        logger.warning("summarize_old_messages failed: %s", exc)
        return ""


def update_memory(conv_id: str, user_msg: str, assistant_msg: str) -> None:
    """
    Add messages to the conversation, trim history, and maintain summaries.

    - Thread-safe and crash-safe.
    - Keeps at most MAX_HISTORY_TURNS user+assistant pairs.
    - If history exceeds the limit, older messages are summarized into the `summary` field.
    """
    global conversations

    # Normalize to strings defensively
    user_text = "" if user_msg is None else str(user_msg)
    assistant_text = "" if assistant_msg is None else str(assistant_msg)

    with _lock:
        conv = conversations.get(conv_id)
        if conv is None:
            conv = {"summary": "", "messages": []}
            conversations[conv_id] = conv

        # Add new messages
        conv["messages"].append({"role": "user", "content": user_text})
        conv["messages"].append({"role": "assistant", "content": assistant_text})

        # Trim message history
        msgs = conv["messages"]
        max_msgs = MAX_HISTORY_TURNS * 2  # user+assistant pairs

        if len(msgs) > max_msgs:
            older = msgs[:-max_msgs]
            keep = msgs[-max_msgs:]

            # Merge old summary with new text
            older_text = " ".join(f"{m['role']}: {m['content']}" for m in older)
            new_summary = f"{conv.get('summary', '')} {older_text}".strip()

            # Enforce summary size limit (keep the most recent part)
            if len(new_summary) > MAX_SUMMARY_CHARS:
                new_summary = new_summary[-MAX_SUMMARY_CHARS:]

            conv["summary"] = new_summary
            conv["messages"] = keep

        conversations[conv_id] = conv
        save_conversations(conversations)
        logger.debug(
            "Updated memory for conv_id=%s (messages=%d, summary_len=%d)",
            conv_id,
            len(conv["messages"]),
            len(conv.get("summary", "")),
        )
