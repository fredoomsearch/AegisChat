"""
HTTP API layer for AEGIS.

Endpoints:
  - GET  /websearch
  - POST /fetch_url
  - POST /chat

Responsibilities:
  - Orchestrate web search + sanitization.
  - Coordinate DB vs memory fallback of conversations.
  - Build context for the LLM and execute generate_response in a thread pool
    to avoid blocking the event loop.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List
from urllib.parse import urlparse

import pytz
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.vision_client import analyze_image_bytes
from app.llm_client_offload import generate_response
from app.llm_utils import build_messages, is_code_question, trim_messages_for_context
from app.memory import get_or_create_conversation, update_memory
from app.schemas import ChatRequest, ChatResponse
from app.tools import (
    fetch_url,
    fetch_url_via_tor,
    is_onion,
    sanitize_web_context,
    web_search_structured,
)


logger = logging.getLogger(__name__)

router = APIRouter()
# Small thread pool to offload blocking LLM calls
_executor = ThreadPoolExecutor(max_workers=2)



#IMAGE ANALYZER 

class ImageAnalysisResponse(BaseModel):
    description: str


# -------------------------------------------------------------------
# Models
# -------------------------------------------------------------------

class WebResult(BaseModel):
    title: str = ""
    snippet: str = ""
    link: str = ""


class FetchUrlRequest(BaseModel):
    url: str


# -------------------------------------------------------------------
# Helper: web search formatting
# -------------------------------------------------------------------

def handle_web_query(message: str) -> str:
    """
    Handle explicit 'web:' prefixed user query, returning a human-readable block.
    """
    query = message[4:].strip()
    if not query:
        return "No encontré resultados."

    try:
        results = web_search_structured(query, num_results=3) or []
    except Exception:
        logger.exception("handle_web_query error")
        results = []

    if not results:
        return "No encontré resultados."

    lines: List[str] = []
    for r in results:
        lines.append(
            f"🟦 {r.get('title', '')}\n"
            f"{r.get('snippet', '')}\n"
            f"{r.get('link', '')}\n"
        )
    return "\n".join(lines).strip()


# -------------------------------------------------------------------
# Language logic
# -------------------------------------------------------------------

def detect_lang_safe(text: str) -> str:
    """
    Best-effort language detection (es/en). Defaults to 'en' if unknown.
    """
    try:
        stripped = (text or "").strip()
        if len(stripped) < 4:
            return "en"

        from langdetect import detect  # type: ignore

        d = detect(stripped)
        if d.startswith("es"):
            return "es"
        if d.startswith("en"):
            return "en"
    except Exception:
        # Any issue with langdetect → default to English
        logger.debug("langdetect failed; defaulting to 'en'", exc_info=True)
    return "en"


def language_pack(lang: str) -> Dict[str, str]:
    """
    Small language pack for system prompts and web-mode instructions.
    """
    if lang == "es":
        return {
            "system": (
                "Nunca inventes hechos. Solo usa datos verificables. "
                "Si las fuentes web no tienen fecha, dilo explícitamente. "
                "Responde en español si el usuario escribe en español."
            ),
            "mode_web": (
                "Usando SOLO los resultados web proporcionados, responde la consulta del usuario."
            ),
        }

    return {
        "system": (
            "Never invent facts. Only use verifiable data. "
            "If web sources have no dates, explicitly say so. "
            "Reply in English when the user speaks English."
        ),
        "mode_web": "Using ONLY the provided web results, answer the user's query.",
    }


def needs_web_search(message: str) -> bool:
    """
    Simple heuristic to decide if we should auto-trigger a web search.
    """
    keywords = [
        "dólar", "dolar",
        "precio",
        "hora",
        "fecha",
        "colombia",
        "usd",
        "$",
        "presidente",
        "president",
    ]
    msg = (message or "").lower()
    return any(k in msg for k in keywords)


# -------------------------------------------------------------------
# World time pack
# -------------------------------------------------------------------

def build_world_time_pack(lang: str) -> str:
    """
    Build a small inline world-time cheat sheet for the LLM.
    """
    zones = {
        "Colombia": "America/Bogota",
        "Mexico": "America/Mexico_City",
        "Argentina": "America/Argentina/Buenos_Aires",
        "New York": "America/New_York",
        "Los Angeles": "America/Los_Angeles",
        "London": "Europe/London",
        "Madrid": "Europe/Madrid",
        "Berlin": "Europe/Berlin",
        "Tokyo": "Asia/Tokyo",
        "Sydney": "Australia/Sydney",
        "Dubai": "Asia/Dubai",
        "Singapore": "Asia/Singapore",
    }

    now_lines: List[str] = []
    for label, zone in zones.items():
        tz = pytz.timezone(zone)
        now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        now_lines.append(f"{label}: {now_str} ({zone})")

    if lang == "es":
        return (
            "Tiempos actuales en el mundo:\n"
            + "\n".join(now_lines)
            + "\n\nSi el usuario pregunta la hora en un país específico, usa estos tiempos."
        )

    return (
        "Current world times:\n"
        + "\n".join(now_lines)
        + "\n\nIf the user asks for time in a specific country, use these values."
    )


# -------------------------------------------------------------------
# Endpoints
# -------------------------------------------------------------------

@router.get("/websearch", response_model=List[WebResult])
async def websearch(
    q: str = Query(..., min_length=1),
    n: int = Query(3, ge=1, le=10),
) -> List[WebResult] | JSONResponse:
    """
    Thin wrapper around web_search_structured → returns structured web results.
    """
    try:
        raw = web_search_structured(q, num_results=n) or []
        return [
            WebResult(
                title=r.get("title", "") or "",
                snippet=r.get("snippet", "") or "",
                link=r.get("link", "") or "",
            )
            for r in raw
        ]
    except Exception as e:
        logger.exception("websearch error")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/fetch_url")
async def fetch_url_endpoint(req: FetchUrlRequest):
    """
    Fetch a URL via clearnet or Tor (.onion) and return cleaned text.
    """
    url = (req.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="missing url")

    parsed = urlparse(url)
    if not parsed.scheme:
        # default to HTTPS if scheme missing
        url = "https://" + url.lstrip("/")

    # .onion via Tor
    if is_onion(url):
        res = fetch_url_via_tor(url)
        if not isinstance(res, dict):
            return {"status": None, "text": str(res)[:200000]}

        if not res.get("ok"):
            raise HTTPException(status_code=502, detail=res.get("error"))

        return {
            "status": res.get("status"),
            "text": res.get("text", "")[:200000],
        }

    # Normal HTTPS fetch
    res = fetch_url(url)
    if isinstance(res, dict):
        if not res.get("ok"):
            raise HTTPException(status_code=502, detail=res.get("error"))
        return {
            "status": res.get("status"),
            "text": res.get("text", ""),
        }

    text = str(res)
    if text.startswith("(error"):
        raise HTTPException(status_code=502, detail=text)

    return {"status": 200, "text": text}


@router.post("/analyze_image", response_model=ImageAnalysisResponse)
async def analyze_image_endpoint(
    file: UploadFile = File(...),
    lang: str = Form("es"),
) -> ImageAnalysisResponse:
    try:
        if not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="File must be an image.")

        image_bytes = await file.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="Empty file.")

        lang_norm = "es" if (lang or "es").lower().startswith("es") else "en"
        description = analyze_image_bytes(image_bytes, lang=lang_norm)

        return ImageAnalysisResponse(description=description)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("analyze_image_endpoint failed: %s", exc)
        raise HTTPException(status_code=500, detail="Error analyzing image.")




# -------------------------------------------------------------------
# Chat endpoint
# -------------------------------------------------------------------

@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    """
    Main chat endpoint.

    - Try to use DB-backed conversation store if available.
    - If DB layer is unavailable, fall back to JSON-backed memory store.
    - Run the heavy LLM call in a ThreadPoolExecutor.
    """
    db = None
    db_available = False
    crud = None

    # Lazy DB import + session open
    try:
        from app.db.base import SessionLocal
        from app.db import crud_conversation

        db = SessionLocal()
        crud = crud_conversation
        db_available = True
    except Exception:
        logger.debug(
            "DB not available; falling back to memory-only conversations.",
            exc_info=True,
        )
        db_available = False

    try:
        # ------------------------------------------------------------------
        # Conversation id: reuse or create
        # ------------------------------------------------------------------
        if body.conversation_id:
            conv_id = body.conversation_id
        else:
            conv_id = (
                crud.create_conversation(db, None)
                if db_available
                else get_or_create_conversation(None)
            )

        new_user_message = (body.message or "").strip()
        if not new_user_message:
            return ChatResponse(
                response="No message received.",
                conversation_id=conv_id,
            )

        logger.info("Incoming message: %s (conv=%s)", new_user_message, conv_id)

        # ------------------------------------------------------------------
        # Detect "analyze:" mode
        #   - If starts with analyze:, treat the rest as TEXT to analyze
        # ------------------------------------------------------------------
        is_analysis = new_user_message.lower().startswith("analyze:")
        analysis_content = ""
        if is_analysis:
            # Everything after "analyze:" is the body to analyze
            analysis_content = new_user_message.split(":", 1)[1].lstrip()

        # ------------------------------------------------------------------
        # Persist user message (best-effort)
        # ------------------------------------------------------------------
        if db_available and crud is not None and db is not None:
            try:
                crud.append_message(db, conv_id, "user", new_user_message)
            except Exception:
                logger.exception(
                    "DB append (user) failed; continuing with memory fallback."
                )

        # ------------------------------------------------------------------
        # Language and world-time packs
        # ------------------------------------------------------------------
        lang = getattr(body, "lang", None) or detect_lang_safe(new_user_message)
        pack = language_pack(lang)
        world_time_content = build_world_time_pack(lang)

        # ------------------------------------------------------------------
        # Branch 1: ANALYSIS MODE
        # ------------------------------------------------------------------
        if is_analysis:
            # Safe analysis prompt; we analyze content but don't execute it.
            messages_for_llm: List[Dict[str, str]] = [
                {"role": "system", "content": pack["system"]},
                {"role": "system", "content": world_time_content},
                {
                    "role": "user",
                    "content": (
                        "Analyze the following content in detail and explain what it does, complete your work step by step:\n\n"
                       
                        + analysis_content
                    ),
                },
            ]

        # ------------------------------------------------------------------
        # Branch 2: NORMAL FLOW (chat / web / url)
        # ------------------------------------------------------------------
        else:
            force_web = False
            user_msg_for_llm = new_user_message
            web_context = ""

            # Explicit web: prefix
            if new_user_message.lower().startswith("web:"):
                force_web = True
                user_msg_for_llm = new_user_message[4:].strip()

            # Auto web-search (heuristic or forced)
            if force_web or needs_web_search(user_msg_for_llm):
                try:
                    raw_results = web_search_structured(
                        user_msg_for_llm,
                        num_results=3,
                    ) or []
                    if raw_results:
                        web_context = "\n\n".join(
                            f"[{i}] {r.get('title')}\n"
                            f"{r.get('snippet')}\n"
                            f"{r.get('link')}"
                            for i, r in enumerate(raw_results, 1)
                        )
                        web_context = sanitize_web_context(web_context, 1200)
                        # In web mode, explicitly instruct to use only those results
                        user_msg_for_llm = pack["mode_web"]
                except Exception as e:
                    logger.exception("Auto web search failed")
                    web_context = f"(error fetching web info: {e})"

            # Build message history (system + summary + retrieval)
            new_messages = build_messages(conv_id, user_msg_for_llm, web_context)

            # Inject language system + world time at the very front
            new_messages.insert(0, {"role": "system", "content": pack["system"]})
            new_messages.insert(1, {"role": "system", "content": world_time_content})

            # Trim to avoid exploding context
            trimmed, _ = trim_messages_for_context(
                new_messages,
                web_context,
                max_total_chars=7000,
            )
            messages_for_llm = trimmed[-12:] if trimmed else new_messages[-1:]

            # If we have web context, add explicit instructions and original question
            if web_context:
                messages_for_llm.append(
                    {"role": "system", "content": f"Resultados web:\n{web_context}"}
                )
                messages_for_llm.append(
                    {
                        "role": "user",
                        "content": (
                            f"{pack['mode_web']}\n\n"
                            f"Pregunta original del usuario: {new_user_message}"
                        ),
                    }
                )

        # ------------------------------------------------------------------
        # Decide token budget
        # ------------------------------------------------------------------
        if is_analysis:
            # Analysis: medium-length answer
            max_tokens_for_this = 700
        else:
            # For code questions, allow more space
            if is_code_question(new_user_message):
                max_tokens_for_this = 1324
            else:
                max_tokens_for_this = 700

        # ------------------------------------------------------------------
        # LLM call (offloaded to thread pool)
        # ------------------------------------------------------------------
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                _executor,
                lambda: generate_response(
                    messages_for_llm,
                    max_new_tokens=max_tokens_for_this,
                ),
            )
        except Exception:
            traceback.print_exc()
            resp = "(error generating response)"

        # ------------------------------------------------------------------
        # Save assistant response
        # ------------------------------------------------------------------
        try:
            if db_available and crud is not None and db is not None:
                try:
                    crud.append_message(db, conv_id, "assistant", resp)
                except Exception:
                    logger.exception(
                        "DB append (assistant) failed; updating memory instead."
                    )
                    update_memory(conv_id, new_user_message, resp)
            else:
                update_memory(conv_id, new_user_message, resp)
        except Exception:
            logger.exception("Memory save failed")

        logger.info(
            "Reply (conv=%s): %s",
            conv_id,
            resp[:300].replace("\n", " "),
        )

        return ChatResponse(response=resp, conversation_id=conv_id)

    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                logger.exception("Failed to close DB session cleanly.")
