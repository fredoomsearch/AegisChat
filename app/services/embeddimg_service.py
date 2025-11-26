"""
Embedding provider wrapper.

Behavior:
 - Tries to use a local sentence-transformers model (configurable via AEGIS_ST_MODEL).
 - If that's not available, falls back to OpenAI embeddings (requires OPENAI_API_KEY / AEGIS_OPENAI_KEY).
 - Lazy-initializes providers on first call to avoid heavy imports at module import time.
 - Thread-safe initialization, small retry/backoff for remote calls.

Public:
    EMBEDDING_DIM: Optional[int]
    embed_texts(texts: List[str]) -> List[List[float]]

API and names are preserved for compatibility with the rest of AEGIS.
"""
from __future__ import annotations

import logging
import os
import time
from threading import Lock
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# Public symbol (may be None until first successful embedding)
EMBEDDING_DIM: Optional[int] = None

# Internal provider handles / factories
_st_model = None
_openai_client = None
_provider_init_lock = Lock()
_embed_fn: Optional[Callable[[List[str]], List[List[float]]]] = None


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def _set_embedding_dim_from_vector(vec: List[float]) -> None:
    """
    Initialize EMBEDDING_DIM from a single embedding vector if not already set.
    """
    global EMBEDDING_DIM
    if EMBEDDING_DIM is None and vec:
        try:
            EMBEDDING_DIM = len(vec)
            logger.info("EMBEDDING_DIM set to %d", EMBEDDING_DIM)
        except Exception:
            logger.debug("Failed to set EMBEDDING_DIM from vector", exc_info=True)


# -------------------------------------------------------------------
# sentence-transformers provider
# -------------------------------------------------------------------
def _init_sentence_transformers() -> Callable[[List[str]], List[List[float]]]:
    """
    Lazy initialize sentence-transformers model. Returns an embed(texts) callable.

    Raises:
        Exception if library or model cannot be loaded.
    """
    global _st_model, _embed_fn, EMBEDDING_DIM
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception as e:
        logger.debug("sentence-transformers import failed: %s", e)
        raise

    model_name = os.getenv("AEGIS_ST_MODEL", "all-MiniLM-L6-v2")
    logger.info("Initializing sentence-transformers model: %s", model_name)
    _st_model = SentenceTransformer(model_name)
    EMBEDDING_DIM = _st_model.get_sentence_embedding_dimension()
    logger.info("Local sentence-transformers loaded (dim=%s)", EMBEDDING_DIM)

    def _embed_st(texts: List[str]) -> List[List[float]]:
        # returns numpy array; convert to list of lists
        arr = _st_model.encode(
            texts,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        if arr is not None:
            first = arr[0].tolist() if hasattr(arr[0], "tolist") else list(arr[0])
            _set_embedding_dim_from_vector(first)
            return arr.tolist()
        return []

    _embed_fn = _embed_st
    return _embed_fn


# -------------------------------------------------------------------
# OpenAI provider
# -------------------------------------------------------------------
def _init_openai() -> Callable[[List[str]], List[List[float]]]:
    """
    Lazy initialize OpenAI client and return embed(texts) callable.

    Env:
      - OPENAI_API_KEY / OPENAI_KEY / AEGIS_OPENAI_KEY
      - AEGIS_OPENAI_EMBED_MODEL (default: text-embedding-3-small)
    """
    global _openai_client, _embed_fn

    try:
        import openai  # type: ignore
    except Exception as e:
        logger.debug("openai import failed: %s", e)
        raise

    key = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("OPENAI_KEY")
        or os.getenv("AEGIS_OPENAI_KEY")
    )
    if not key:
        raise RuntimeError("OPENAI_API_KEY / AEGIS_OPENAI_KEY not set for OpenAI fallback")

    openai.api_key = key  # type: ignore[attr-defined]
    model = os.getenv("AEGIS_OPENAI_EMBED_MODEL", "text-embedding-3-small")
    logger.info("Using OpenAI embeddings model=%s", model)

    _openai_client = openai

    def _embed_openai(texts: List[str]) -> List[List[float]]:
        # small retry/backoff for network reliability
        tries = 3
        backoff = 1.0
        last_exc: Optional[Exception] = None

        for attempt in range(tries):
            try:
                resp = _openai_client.Embedding.create(  # type: ignore[attr-defined]
                    model=model,
                    input=texts,
                )
                data = resp.get("data", [])
                if not data:
                    raise RuntimeError("empty embedding response from OpenAI")
                embeddings: List[List[float]] = [d["embedding"] for d in data]
                if embeddings:
                    _set_embedding_dim_from_vector(embeddings[0])
                return embeddings
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "OpenAI embed attempt %d/%d failed: %s",
                    attempt + 1,
                    tries,
                    exc,
                )
                if attempt + 1 < tries:
                    time.sleep(backoff)
                    backoff *= 2.0

        # If we exit the loop, all attempts failed
        logger.exception("OpenAI embedding failed after retries: %s", last_exc)
        raise last_exc or RuntimeError("OpenAI embedding failed")

    _embed_fn = _embed_openai
    return _embed_fn


# -------------------------------------------------------------------
# Provider discovery
# -------------------------------------------------------------------
def _discover_provider() -> Callable[[List[str]], List[List[float]]]:
    """
    Choose and initialize the best available provider.

    Preference:
      1) sentence-transformers local model
      2) OpenAI embeddings

    Raises:
        RuntimeError if no provider is available.
    """
    global _embed_fn

    with _provider_init_lock:
        if _embed_fn is not None:
            return _embed_fn

        # 1) try sentence-transformers
        try:
            fn = _init_sentence_transformers()
            logger.info("Embedding provider: sentence-transformers (local)")
            return fn
        except Exception:
            logger.debug("sentence-transformers unavailable; trying OpenAI")

        # 2) try OpenAI
        try:
            fn = _init_openai()
            logger.info("Embedding provider: OpenAI")
            return fn
        except Exception as e:
            logger.debug("OpenAI embedding unavailable: %s", e)

        # nothing available
        raise RuntimeError(
            "No embedding provider available. "
            "Install sentence-transformers or set OPENAI_API_KEY / AEGIS_OPENAI_KEY."
        )


# -------------------------------------------------------------------
# Public function
# -------------------------------------------------------------------
def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Return embeddings for a list of texts as list[list[float]].

    Raises:
      - RuntimeError if no provider is available.
      - ValueError if input is empty or invalid.
    """
    if not texts:
        return []

    global _embed_fn

    # provider discovery (lazy)
    if _embed_fn is None:
        _embed_fn = _discover_provider()

    # call the provider
    try:
        embeddings = _embed_fn(texts)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Embedding provider failed: %s", exc)
        # re-try provider discovery once (in case of transient import/config issues)
        with _provider_init_lock:
            _embed_fn = None
            _embed_fn = _discover_provider()
        embeddings = _embed_fn(texts)

    # normalize types: ensure list of lists of float
    out: List[List[float]] = []
    for vec in embeddings:
        # support numpy arrays and other sequences
        if hasattr(vec, "tolist"):
            vec_list = vec.tolist()
        else:
            vec_list = list(vec)
        out.append([float(x) for x in vec_list])

    # set EMBEDDING_DIM if unset
    if out and EMBEDDING_DIM is None:
        _set_embedding_dim_from_vector(out[0])

    return out
