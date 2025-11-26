"""
Central config for AEGIS.

Reads environment variables (via python-dotenv) and exposes simple
module-level constants used by the rest of the app.

Public constants (unchanged API):
  - DATA_DIR
  - CONV_FILE
  - MAX_HISTORY_TURNS
  - MAX_SUMMARY_CHARS
  - MAX_NEW_TOKENS
  - MODEL_PATH
  - OPENAI_API_KEY
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load .env into process environment (no error if missing)
load_dotenv()


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _get_int_env(name: str, default: int) -> int:
    """
    Read an int environment variable with a safe fallback.

    - If unset or empty → default
    - If invalid (e.g. "abc") → log a warning and return default
    """
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid int for %s=%r, using default %d", name, raw, default)
        return default


# -------------------------------------------------------------------
# Directories & files
# -------------------------------------------------------------------

DATA_DIR: str = os.getenv("AEGIS_DATA_DIR", "data")
CONV_FILE: str = os.path.join(DATA_DIR, "conversations.json")

# -------------------------------------------------------------------
# Conversation settings
# -------------------------------------------------------------------

# How many turns to keep in detailed history (older ones may be summarized)
MAX_HISTORY_TURNS: int = _get_int_env("AEGIS_MAX_HISTORY_TURNS", 10) 

# Max chars for the lightweight conversation summary used in prompts
MAX_SUMMARY_CHARS: int = _get_int_env("AEGIS_MAX_SUMMARY_CHARS", 1400)

# -------------------------------------------------------------------
# LLM settings
# -------------------------------------------------------------------

# Default max new tokens for local WizardCoder generation
MAX_NEW_TOKENS: int = _get_int_env("AEGIS_MAX_NEW_TOKENS", 800)

# Path to local GGUF model (WizardCoder or otherwise)
MODEL_PATH: str = os.getenv(
    "AEGIS_MODEL_PATH",
    "./models/wizardcoder-python-7b-v1.0.Q5_K_M.gguf",
)

# -------------------------------------------------------------------
# OpenAI (optional)
# -------------------------------------------------------------------

OPENAI_API_KEY: str | None = (
    os.getenv("OPENAI_API_KEY")
    or os.getenv("OPENAI_KEY")
    or None
)


def config_snapshot() -> Dict[str, Any]:
    """
    Small helper for debugging / logging.

    NOTE: does NOT return the actual API key, only whether it is set.
    """
    return {
        "DATA_DIR": DATA_DIR,
        "CONV_FILE": CONV_FILE,
        "MAX_HISTORY_TURNS": MAX_HISTORY_TURNS,
        "MAX_SUMMARY_CHARS": MAX_SUMMARY_CHARS,
        "MAX_NEW_TOKENS": MAX_NEW_TOKENS,
        "MODEL_PATH": MODEL_PATH,
        "OPENAI_API_KEY_SET": bool(OPENAI_API_KEY),
    }
