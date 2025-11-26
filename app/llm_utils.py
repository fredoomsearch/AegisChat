"""
LLM utilities for AEGIS.

Responsibilities:
- Lightweight conversation summarization (for prompts).
- Truncation / trimming of messages + web context under a char budget.
- Simple code-intent detection to decide when to use retrieval.
- Construction of the final messages list to send to the LLM backend.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Tuple
import logging
import re

from app.memory import conversations  # expected: {conv_id: {"summary": str, "messages": [...]}}
from app.config import MAX_SUMMARY_CHARS

logger = logging.getLogger(__name__)

# --------------------------------
# RETRIEVAL SERVICE (BEST-EFFORT)
# --------------------------------
try:
    from app.services.retrieval_service import query as retrieval_query  # type: ignore

    RETRIEVAL_AVAILABLE = True
except Exception:
    retrieval_query = None
    RETRIEVAL_AVAILABLE = False
    logger.info("Retrieval service unavailable; continuing without retrieval.")


# ============================================================================
# SUMMARIZATION
# ============================================================================

def summarize_conversation(conv_id: str, max_chars: int = MAX_SUMMARY_CHARS) -> str:
    """
    Lightweight summarizer based on the last few turns stored in memory.

    NOTE: This does NOT call the LLM; it's just a compact text join of recent
    messages, used as a cheap context snippet.
    """
    conv = conversations.get(conv_id, {})
    msgs = conv.get("messages", [])
    if not msgs:
        return ""

    recent = msgs[-8:]
    text = " | ".join(
        f"{m.get('role', '')}: {m.get('content', '')}" for m in recent
    )
    return text[:max_chars]


# ============================================================================
# TEXT TRUNCATION & CONTEXT TRIMMING
# ============================================================================

def truncate_text(text: str, max_chars: int) -> str:
    """
    Truncate text to `max_chars` characters from the end (keeps most recent part).
    """
    if not text or max_chars <= 0:
        return ""
    return text if len(text) <= max_chars else text[-max_chars:]


def trim_messages_for_context(
    messages: List[Dict[str, str]],
    web_context: str = "",
    max_total_chars: int = 6000,
) -> Tuple[List[Dict[str, str]], str]:
    """
    Ensure messages + web_context fit inside the character budget.

    Rules:
      - If there are messages, always preserve the first message as "system".
      - Then keep the most recent messages that fit the remaining budget.
      - Whatever budget is left goes to web_context (trimmed from the end).
      - If there are no messages, web_context gets the full budget.

    Returns:
      (trimmed_messages, trimmed_web_context)
    """
    if max_total_chars <= 0:
        return [], ""

    if not messages:
        # No messages, all budget goes to web context
        return [], truncate_text(web_context, max_total_chars)

    # Separate system and the rest
    system_msg = messages[0]
    rest = messages[1:]

    # Start with the system message cost
    system_len = len(system_msg.get("content", ""))
    if system_len >= max_total_chars:
        # System alone consumes the budget; drop others and web context
        logger.debug(
            "System message alone exceeds max_total_chars (%d); trimming system only.",
            max_total_chars,
        )
        sys_trimmed = system_msg.copy()
        sys_trimmed["content"] = truncate_text(
            sys_trimmed.get("content", ""), max_total_chars
        )
        return [sys_trimmed], ""

    remaining_budget = max_total_chars - system_len
    kept: List[Dict[str, str]] = []

    # Keep newest messages first within remaining budget
    total_used = system_len
    for m in reversed(rest):
        content_len = len(m.get("content", ""))
        if content_len <= 0:
            continue
        if total_used + content_len > max_total_chars:
            break
        kept.append(m)
        total_used += content_len

    kept.reverse()
    final_messages = [system_msg] + kept

    # Whatever is left goes to web_context
    remaining_for_web = max_total_chars - total_used
    trimmed_web = truncate_text(web_context, max(0, remaining_for_web))

    return final_messages, trimmed_web


# ============================================================================
# CODE QUESTION DETECTION
# ============================================================================

_CODE_KEYWORDS = [
    # languages / frameworks
    "python", "fastapi", "django", "flask", "javascript", "typescript",
    "react", "angular", "c++", "java", "spring", "node", "express",
    "sql", "postgres", "postgresql", "mysql", "sqlite",
    "dockerfile", "yaml", "json",
    # errors / debugging
    "stack trace", "traceback", "error", "exception", "bug", "fix",
    # code structure
    "repo", "repository", "function", "class",
    ".py", ".js", ".ts", ".jsx", ".tsx",
    # devops
    "deploy", "lambda", "aws", "ci", "pipeline",
]


def is_code_question(text: str) -> bool:
    """
    Basic intent detection to activate retrieval for technical/code questions.
    Very cheap heuristic: keyword-based + simple pattern checks.
    """
    t = (text or "").lower()

    if any(kw in t for kw in _CODE_KEYWORDS):
        return True

    # Mark code fences
    if "```" in t:
        return True

    # Pattern like: def foo(, class Bar, import something, from x import y
    if re.search(r"\b(def|class|import|from)\b", t):
        return True

    return False


# ============================================================================
# BUILD MESSAGES FOR LLM INPUT
# ============================================================================

def build_messages(
    conv_id: str,
    new_user_message: str,
    web_context: str = "",
    max_total_chars: int = 6000,  # kept for compatibility, trimming is done by caller
) -> List[Dict]:
    """
    Build robust, context-aware messages for LLM:

      - Dynamic system prompt
      - Conversation summary (lightweight, from memory)
      - Recent messages from in-memory store
      - Retrieval context if code-related
      - Web context (sanitized & HTTPS-guarded)
      - Current user message with timestamp

    Returns:
      List[{"role": ..., "content": ...}] ready to be sent to LLM backend.
    """
    conv = conversations.get(conv_id, {"messages": []})
    msgs = conv.get("messages", []) or []

    # ---- Conversation summary ----
    summary_snippet = summarize_conversation(conv_id)

    # ---- Dynamic system prompt ----
    system_prompt_parts = [
        # Identity / style
        "Always complete all your answers with quality, structure, and clear organization.",
        "You are AEGIS, an AI assistant similar to ChatGPT.",
        "You always reply in the same language as the user (Spanish or English).",
        "Be friendly, clear, and practical. Avoid being too verbose unless the user explicitly asks for a lot of detail.",
        "For programming questions, always include at least one runnable code example in a fenced markdown code block and a short explanation.",

        # identity & high-level role
        "You are AEGIS — a precise, cautious, and helpful AI assistant that adapts its style to the user's language and preferences.",

        # behavior guardrails
        "Answer concisely by default. Do not invent facts or hallucinate. If you do not know, say 'I don't know' or 'insufficient information'.",
        "Use ONLY the context provided in the conversation, retrieved project context, and verified HTTPS web context. Do not assume external facts not provided.",

        # citation & evidence rules
        "When using web or retrieved context, you may use inline numbered references like [1], [2], and at the end you can list sources with title, short reason for relevance, and the HTTPS link.",
        "If a cited web source lacks a publication date, explicitly state 'source date unknown'. If a source seems low-quality or irrelevant, flag it and lower your confidence.",

        # answer structure + verbosity policy
        "Provide a one-sentence TL;DR summary first, then a short answer (2–4 sentences). "
        "If the user asks for more, you can add a 'Details' section with numbered steps, examples, or code snippets as appropriate.",
        "Default style: neutral, professional, and slightly concise. Match the user's formality and language. "
        "If the user says 'be brief', respond in <= 2 sentences; if they say 'explain like I'm five', simplify terminology and use metaphors.",

        # code-specific guidance
        "For code questions: ALWAYS include at least one runnable code block in markdown triple backticks. Keep explanations short and put most of the answer as code.",
        "If retrieval context (project files, repo snippets) is provided, prefer that over general web knowledge and cite it as [RETRIEVAL-1], [RETRIEVAL-2].",

        # web / security / allowed sources
        "Only use HTTPS sources. If the user requests HTTP-only content, refuse and briefly explain that only HTTPS is allowed for security.",
        "You can't provide disallowed or dangerous content (malware, instructions for illegal activities, or high-risk bio/chemical instructions). "
        "For medical, legal, or safety-critical queries, provide general information and recommend consulting a qualified professional.",

        # confidence & uncertainty reporting
        "At the end, include a concise confidence indicator (High / Medium / Low) and one sentence describing any key assumptions "
        "or missing data required for a more confident answer.",

        # memory & privacy
        "Do not store or expose sensitive personal data. If the user asks you to 'remember' something personal, ask for confirmation before saving it to persistent memory "
        "and summarize clearly what will be stored.",

        # conversational control & tokens
        "Keep context size and token usage in mind: prioritize recent turns and retrieval/web context. If context is large, summarize older turns and indicate what was summarized.",

        # security / exploits / learning (defensive focus)
        "You can help the user learn about exploits cybersecurity, vulnerabilities, and defenses in a high-level and practical way.",
        "You can't provide step-by-step instructions to exploit real systems, write malware, bypass security controls, or perform illegal hacking.",
        "When explaining vulnerabilities, focus on how they work conceptually and especially how to detect, use, prevent and fix them."
        "ALWAYS BUILD THE CODE"
    ]

    # Fine-tune behavior when it's a code question
    if is_code_question(new_user_message):
        system_prompt_parts.append(
            "The current user message appears to be a technical/programming question. "
            "Respond like a senior engineer pair-programming with the user:\n\n"
            "1) First restate the goal in one short sentence.\n"
            "2) Include concrete commands or steps to run or reproduce it (for example: how to install dependencies, "
            "how to run the program or tests, and which files to create or edit).\n"
            "3) Briefly explain the key lines and any non-obvious decisions in clear, plain language.\n"
            "4) Prefer correct, working code over brevity; avoid pseudo-code unless the user explicitly asks for it.\n"
            "5) If important details are missing (framework, versions, error messages), state your assumptions "
            "instead of silently guessing, and keep the answer adaptable.\n"
            "6) When you output code, ensure that functions, classes, and blocks are complete and syntactically valid. "
            "Do not stop in the middle of a code block; close any opened scopes before finishing."
            "Make your work complete ever"
        )

    if summary_snippet:
        system_prompt_parts.append(
            f"Compressed recent conversation context (not exhaustive): {summary_snippet}"
        )

    system_prompt = "\n".join(system_prompt_parts)
    messages: List[Dict] = [{"role": "system", "content": system_prompt}]

    # ---- Add web context (if any) ----
    if web_context:
        wc_lower = web_context.lower()
        if "http://" in wc_lower and "https://" not in wc_lower:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "⚠️ SECURITY WARNING: Only HTTPS sources are allowed. "
                        "Do not use blocked URLs."
                    ),
                }
            )
        else:
            messages.append(
                {
                    "role": "system",
                    "content": f"External web context (HTTPS verified):\n{web_context}",
                }
            )

    # ---- Add retrieval context if code-related ----
    if RETRIEVAL_AVAILABLE and retrieval_query and is_code_question(new_user_message):
        try:
            chunks = retrieval_query(new_user_message, top_k=6)  # type: ignore[arg-type]
        except Exception as exc:
            logger.exception("Retrieval failed: %s", exc)
            chunks = []

        if chunks:
            ctx_text = "\n\n".join(
                f"--- SOURCE: {c.get('source', '?')} ---\n{c.get('text', '')}"
                for c in chunks
            )
            messages.append(
                {
                    "role": "system",
                    "content": f"Relevant project context (retrieved):\n{ctx_text}",
                }
            )

    # ---- Add recent conversation turns from memory ----
    MAX_RECENT = 12
    recent_msgs = msgs[-MAX_RECENT:]
    for m in recent_msgs:
        messages.append(
            {
                "role": m.get("role", "user"),
                "content": m.get("content", ""),
            }
        )

    # ---- Add current user message with timestamp ----
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    messages.append(
        {
            "role": "user",
            "content": f"[{timestamp}] {new_user_message}",
        }
    )

    # No trimming here; caller can apply trim_messages_for_context
    return messages
