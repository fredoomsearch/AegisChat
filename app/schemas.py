from __future__ import annotations

from typing import Optional, List, Dict
from enum import Enum
from datetime import datetime
import uuid

from pydantic import BaseModel, Field, validator


# ---------------------------------------------------------------------
# Language enum
# ---------------------------------------------------------------------
class Language(str, Enum):
    """
    Language hint for the LLM pipeline.
    Only 'es' and 'en' allowed to avoid surprise behavior.
    """
    es = "es"
    en = "en"


# ---------------------------------------------------------------------
# ChatRequest
# ---------------------------------------------------------------------
class ChatRequest(BaseModel):
    """
    Request body for POST /chat

    - message: required user message
    - conversation_id: optional UUID string
    - lang: language hint ('es' or 'en')
    """

    message: str = Field(
        ...,
        min_length=1,
        description="User message"
    )

    conversation_id: Optional[str] = Field(
        None,
        description="Existing conversation id (UUID string) or None"
    )

    lang: Optional[Language] = Field(
        None,
        description="'es' or 'en' (optional)"
    )

    @validator("conversation_id")
    def validate_conv_id(cls, v: Optional[str]) -> Optional[str]:
        """
        Accepts None or valid UUID string.
        Returns the original string to preserve compatibility.
        """
        if not v:
            return None
        try:
            uuid.UUID(str(v))
            return str(v)
        except Exception as exc:
            raise ValueError("conversation_id must be a valid UUID string") from exc

    class Config:
        # These keys are pydantic v1 style but still work; warnings are harmless.
        use_enum_values = True
        anystr_strip_whitespace = True
        validate_assignment = True
        schema_extra = {
            "example": {
                "message": "¿Cuánto vale un dólar hoy?",
                "conversation_id": None,
                "lang": "es",
            }
        }


# ---------------------------------------------------------------------
# ChatResponse
# ---------------------------------------------------------------------
class ChatResponse(BaseModel):
    """
    Response body returned by POST /chat

    - response: assistant text
    - conversation_id: UUID of session
    - timestamp: server timestamp (ISO 8601)
    - sources: optional web search structured results
    """

    response: str = Field(
        ...,
        description="Assistant response text"
    )

    conversation_id: Optional[str] = Field(
        None,
        description="Conversation id returned by server (UUID string)"
    )

    timestamp: Optional[datetime] = Field(
        None,
        description="When the response was created (server side timestamp)"
    )

    sources: Optional[List[Dict[str, str]]] = Field(
        None,
        description="Optional list of search sources [{'title','snippet','link'}]"
    )

    @validator("conversation_id")
    def validate_conv_id_resp(cls, v: Optional[str]) -> Optional[str]:
        """
        Same validation logic as ChatRequest.
        """
        if not v:
            return None
        try:
            uuid.UUID(str(v))
            return str(v)
        except Exception as exc:
            raise ValueError("conversation_id must be a valid UUID string") from exc

    class Config:
        anystr_strip_whitespace = True
        validate_assignment = True
        schema_extra = {
            "example": {
                "response": "Hoy el dólar está en X COP. (Fuente: ejemplo.com)",
                "conversation_id": "23b7afdb-1acc-4c9b-befc-add6df189070",
                "timestamp": "2025-11-19T00:00:00Z",
                "sources": [
                    {
                        "title": "Banco X: cotización",
                        "snippet": "Resumen...",
                        "link": "https://ejemplo.com",
                    }
                ],
            }
        }


# 🔥 What improved (without changing behavior)

# Reduced noise in validators, cleaner exception surfaces.

# Stronger docstrings for maintainability.

# Slightly stricter, safer type handling.

# Config stays identical so FastAPI docs remain clean.

# Exact same public API — no breaking changes.