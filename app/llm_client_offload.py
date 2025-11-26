"""
Local LLM client (WizardCoder-only).

Flow:
  1) Build an instruct-style prompt from chat messages.
  2) Use llama.cpp to run a local GGUF WizardCoder model.
  3) If llama is not available or fails → return a clear placeholder text.

External API (kept the same):
    generate_response(messages: list, max_new_tokens: int | None) -> str
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Dict, List, Optional

from app.config import MAX_NEW_TOKENS, MODEL_PATH
from app.tools import _sanitize_messages

logger = logging.getLogger(__name__)

# ============================================================================
# PROMPT SIZE BUDGET
# ============================================================================

def _default_prompt_char_threshold() -> int:
    """
    Compute a default max prompt size in characters based on the LLM context.

    Rough heuristic: ~4 chars/token, and we keep ~50% of the window
    for the prompt so we leave room for the new tokens.
    """
    ctx = int(os.getenv("AEGIS_LLAMA_CTX", "4096"))
    # 0.5 * ctx_tokens * 4 chars/token
    return int(ctx * 4 * 0.5)


DEFAULT_PROMPT_CHAR_THRESHOLD = int(
    os.getenv("AEGIS_PROMPT_CHAR_THRESHOLD", str(_default_prompt_char_threshold()))
)


# ============================================================================
# SAFE IMPORT OF llama_cpp (WizardCoder local)
# ============================================================================

try:
    from llama_cpp import Llama  # type: ignore
except Exception as e:
    logger.warning("llama_cpp not available or failed to load: %s", e)
    Llama = None  # type: ignore


# ============================================================================
# PROMPT BUILDER
# ============================================================================

def _build_prompt(messages: List[Dict]) -> str:
    """
    Convert chat-style messages into a simple instruct-style prompt that
    WizardCoder / llama.cpp understands well.

    The structure is:
        ### System:
        ...
        ### Instruction:
        ...
        ### Response:
        <model continues here>
    """
    parts: List[str] = []
    for m in messages:
        role = m.get("role", "")
        content = (m.get("content") or "").strip()

        if role == "system":
            parts.append(f"### System:\n{content}")
        elif role == "user":
            parts.append(f"### Instruction:\n{content}")
        elif role == "assistant":
            parts.append(f"### Response:\n{content}")
        else:
            parts.append(f"### {role}:\n{content}")

    # Model will continue after this final Response header.
    return "\n\n".join(parts) + "\n\n### Response:\n"


# ============================================================================
# GPU/CPU — PERFORMANCE TUNING (llama singleton)
# ============================================================================

_llm: Optional[object] = None  # global singleton instance for llama.cpp


def _detect_vram_gb() -> Optional[float]:
    """
    Best-effort VRAM detection via `nvidia-smi`.
    Returns:
        total VRAM in GB (float), or None if detection fails.

    This is ONLY used for auto-selecting n_gpu_layers when the env var
    AEGIS_N_GPU_LAYERS is not set.
    """
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
        )
        txt = out.decode("utf-8", errors="ignore").strip().splitlines()[0]
        mb = float(txt)
        return mb / 1024.0
    except Exception:
        logger.debug("Could not detect VRAM via nvidia-smi", exc_info=True)
        return None


def _choose_n_gpu_layers() -> int:
    """
    Decide how many transformer layers to place on the GPU (n_gpu_layers).

    Priority:
      1) Manual override via env var AEGIS_N_GPU_LAYERS.
      2) Else, auto-select based on detected VRAM.
      3) If VRAM detection fails → 0 (CPU-only).

    For a 3050 Ti 4GB, a good manual starting point is:
        export AEGIS_N_GPU_LAYERS=20
    """
    env_val = os.getenv("AEGIS_N_GPU_LAYERS")
    if env_val is not None:
        try:
            n = int(env_val)
            n = max(0, n)
            logger.info("[llm] Using AEGIS_N_GPU_LAYERS=%d (manual override)", n)
            return n
        except ValueError:
            logger.warning(
                "Invalid AEGIS_N_GPU_LAYERS=%r; falling back to auto-detection.",
                env_val,
            )

    vram = _detect_vram_gb()
    if vram is None:
        logger.info("[llm] No GPU found or nvidia-smi unavailable → CPU only.")
        return 0

    if vram <= 2.0:
        n_layers = 0
    elif vram <= 4.0:
        n_layers = 15
    elif vram <= 6.0:
        n_layers = 30
    else:
        n_layers = 40

    logger.info(
        "[llm] VRAM detected: %.2f GB → n_gpu_layers=%d (auto)",
        vram,
        n_layers,
    )
    return n_layers


def _init_llm():
    """
    Create llama.cpp client using GPU/CPU-balanced settings.
    WizardCoder GGUF model only.
    """
    if Llama is None:
        logger.warning("[llm] llama_cpp not installed or could not load → no local model.")
        return None

    # 1) How many layers go on the GPU
    n_gpu = _choose_n_gpu_layers()  # respects AEGIS_N_GPU_LAYERS if set

    # 2) Context size – 4096 works well for 7B Q5_K_M on ~16 GB RAM
    n_ctx = int(os.getenv("AEGIS_LLAMA_CTX", "4096"))

    # 3) Threads – for 5800H (8C/16T), 8–10 is a good sweet spot
    default_threads = 10
    n_threads = int(os.getenv("AEGIS_LLAMA_THREADS", str(default_threads)))

    # 4) Larger batch improves prefill speed
    default_batch = 612
    n_batch = int(os.getenv("AEGIS_LLAMA_BATCH", str(default_batch)))

    try:
        llm = Llama(
            model_path=MODEL_PATH,
            n_ctx=n_ctx,
            n_threads=n_threads,
            n_gpu_layers=n_gpu,
            n_batch=n_batch,
            verbose=False,
        )
        print(
            f"[llm_client_offload] Loaded local Llama model: {MODEL_PATH} "
            f"(n_ctx={n_ctx}, n_threads={n_threads}, "
            f"n_gpu_layers={n_gpu}, n_batch={n_batch})"
        )
        return llm
    except Exception as e:
        print(f"[llm_client_offload] Failed to init llama_cpp: {e}")
        return None


def _get_llm():
    """
    Return (and lazily initialize) the global llama.cpp instance.

    This ensures the model is loaded only once per process.
    """
    global _llm
    if _llm is None:
        _llm = _init_llm()
    return _llm


# ============================================================================
# LLAMA BACKEND (Wizard-only)
# ============================================================================

def _extract_llama_text(out) -> Optional[str]:
    """
    Normalize llama.cpp output into a plain string.

    Handles different output shapes:
      - raw string
      - dict with 'choices' → 'text' or 'message'
      - dict with 'text' at top level
    """
    # String
    if isinstance(out, str):
        return out.strip()

    # Dict with choices/message/etc.
    if isinstance(out, dict):
        choices = out.get("choices") or []
        if choices:
            first = choices[0]

            msg = first.get("message")
            if isinstance(msg, dict) and msg.get("content"):
                return str(msg["content"]).strip()

            if first.get("text"):
                return str(first["text"]).strip()

            if first.get("content"):
                return str(first["content"]).strip()

        if out.get("text"):
            return str(out["text"]).strip()

        # Fallback: stringify the dict
        return str(out)

    # Anything else → stringify
    return str(out)


def _call_llama(prompt: str, max_tokens: int) -> Optional[str]:
    """
    Safe wrapper around llama.cpp.

    - Calls the global llama instance with the given prompt.
    - Catches context-length errors and generic GGML crashes.
    - Returns a plain string, or None on failure.
    """
    llm = _get_llm()
    if llm is None:
        return None

    try:
        out = llm(prompt, max_tokens=max_tokens)
        return _extract_llama_text(out)
    except ValueError as e:
        msg = str(e).lower()
        if "context" in msg or "exceed" in msg:
            print("[llm_client_offload] llama.cpp context overflow.")
            return None
        return None
    except Exception as e:
        print(f"[llm_client_offload] llama.cpp error: {e}")
        return None


# ============================================================================
# PUBLIC API — generate_response() (Wizard-only)
# ============================================================================

def generate_response(messages: List[Dict], max_new_tokens: Optional[int] = None) -> str:
    """
    Main entrypoint used by the rest of the app.

    - Builds an instruct-style prompt from chat messages.
    - If the prompt is too long → trim to [system + last user] only.
    - Uses ONLY local llama.cpp (WizardCoder). No OpenAI / remote fallback.

    Args:
        messages: list of {"role": "system"|"user"|"assistant", "content": str}
        max_new_tokens: optional override; otherwise uses app.config.MAX_NEW_TOKENS

    Returns:
        Model's response text, or a clear error placeholder string.
    """
    max_tokens = int(max_new_tokens or MAX_NEW_TOKENS)
    messages = _sanitize_messages(messages)

    # Build prompt from the full message history
    prompt = _build_prompt(messages)

    # Enforce char budget to avoid llama context explosions
    if len(prompt) > DEFAULT_PROMPT_CHAR_THRESHOLD:
        sys_msg = next((m for m in messages if m.get("role") == "system"), None)
        last_user = next(
            (m for m in reversed(messages) if m.get("role") == "user"), None
        )

        trimmed: List[Dict] = []
        if sys_msg:
            trimmed.append(sys_msg)
        if last_user:
            trimmed.append(last_user)

        prompt = _build_prompt(trimmed)
        print("[llm_client_offload] Prompt trimmed to system + last user.")

    # Only WizardCoder (llama.cpp) is used here
    out = _call_llama(prompt, max_tokens)
    if out:
        return out

    # If the local model completely fails, be explicit
    return "Local WizardCoder model is not available or failed to respond."



# ============================================================================
# RUNTIME TUNING CHEAT SHEET (SHELL EXAMPLES, NOT EXECUTED BY PYTHON)
# ============================================================================
# These commands are meant to be used in your terminal before running
# uvicorn or test scripts. They do NOT run inside Python.
#
# Example for your Ryzen 7 5800H + 16 GB RAM + RTX 3050 Ti 4 GB:
#
#   # Use GPU more (how many transformer layers are offloaded to GPU)
#   export AEGIS_N_GPU_LAYERS=28     # start with 24; if stable, you can try 28
#
#   # Use a larger context window (more "memory" per prompt, slightly slower)
#   export AEGIS_LLAMA_CTX=4096
#
#   # Use a good number of CPU threads for inference
#   export AEGIS_LLAMA_THREADS=10    # good default for 8C/16T 5800H
#
#   # Optional: increase the char budget before trimming the prompt
#   export AEGIS_PROMPT_CHAR_THRESHOLD=9000
#
# To quickly benchmark outside FastAPI, you can write a small script like:
#
#   python test_llama_speed.py

# # See what you currently have
# env | grep AEGIS

# # Override with sane values for your Ryzen 7 5800H + 16GB RAM + 3050 Ti 4GB
#  export AEGIS_LLAMA_CTX=4096       # bigger context
#  export AEGIS_LLAMA_THREADS=10     # good sweet spot for 8C/16T
#  export AEGIS_N_GPU_LAYERS=15    # 24 layers on the 4GB GPU
#  export AEGIS_LLAMA_BATCH=612      # nice big batch for speed
#  export AEGIS_PROMPT_CHAR_THRESHOLD=6000   # don’t let prompts get too big

# Optional: global default for responses

#
# (Those are just usage examples; nothing below this comment block is executed.)
