"""
FAISS-backed retrieval service.

Public functions (same API):
  - chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> List[str]
  - add_documents(docs: List[Tuple[str, str]]) -> int
  - query(text: str, top_k: int = 6) -> List[dict]

Notas:
  - Mantiene el índice en disco (INDEX_DIR / index.faiss + metadata.json).
  - Si FAISS no está instalado, el servicio se degrada elegantemente:
      * add_documents -> RuntimeError
      * query -> []
  - Usa locks para proteger estado in-memory y escrituras en disco.
  - Escrituras atómicas para metadata JSON (para evitar corrupción).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --------- optional heavy deps ---------
try:
    import faiss  # type: ignore
except Exception:
    faiss = None
    logger.warning(
        "faiss not installed; retrieval service disabled. "
        "Install 'faiss-cpu' to enable semantic search."
    )

# embedding service (must be provided by the project)
from app.services.embedding_service import EMBEDDING_DIM, embed_texts  # type: ignore

# --------- config / files ---------
INDEX_DIR = Path(os.getenv("AEGIS_INDEX_DIR", "./data/faiss_index"))
INDEX_FILE = INDEX_DIR / "index.faiss"
META_FILE = INDEX_DIR / "metadata.json"

# internal state
_index = None  # type: ignore[var-annotated]  # faiss index instance
_meta: Dict[str, object] = {"ids": [], "meta": {}}  # persisted metadata
_state_lock = Lock()  # protects _index and _meta during load/save/modify


# --------- helpers ---------
def _ensure_index_dir() -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, data: object) -> None:
    """
    Write JSON file atomically to avoid corruption on crash.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except Exception:
        logger.exception("Failed to write metadata JSON at %s", path)


def _normalize(vecs):
    """
    Normalize vectors (numpy array-like) to unit length for inner-product / cosine sim.

    Returns:
        float32 numpy array (2D).
    """
    try:
        import numpy as np
    except Exception:
        raise RuntimeError("numpy is required for retrieval normalization")

    arr = np.asarray(vecs, dtype="float32")
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)

    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (arr / norms).astype("float32")


# --------- index lifecycle ---------
def _create_index():
    """
    Create a new FAISS index given EMBEDDING_DIM.
    """
    if EMBEDDING_DIM is None:
        raise RuntimeError("EMBEDDING_DIM is not set; cannot create FAISS index")

    idx = faiss.IndexFlatIP(int(EMBEDDING_DIM))
    logger.info("Created new FAISS IndexFlatIP (dim=%s)", EMBEDDING_DIM)
    return idx


def _load_index() -> None:
    """
    Load index and metadata into memory (idempotent). Protected by _state_lock.
    """
    global _index, _meta

    with _state_lock:
        if _index is not None:
            return

        _ensure_index_dir()

        if faiss is None:
            logger.debug("_load_index skipped: faiss not available")
            return

        if INDEX_FILE.exists() and META_FILE.exists():
            try:
                _index = faiss.read_index(str(INDEX_FILE))
                with META_FILE.open("r", encoding="utf-8") as f:
                    _meta = json.load(f)
                logger.info(
                    "Loaded FAISS index (%d vectors)",
                    len(_meta.get("ids", [])),  # type: ignore[arg-type]
                )
                return
            except Exception as e:
                logger.exception(
                    "Failed to load FAISS index/metadata; will recreate. %s", e
                )

        # Create new empty index + metadata
        _index = _create_index()
        _meta = {"ids": [], "meta": {}}
        logger.info("Initialized empty FAISS index and metadata.")


def _save_index() -> None:
    """
    Persist index and metadata atomically. No-op if index not initialized.
    """
    global _index, _meta
    with _state_lock:
        if _index is None:
            logger.debug("_save_index: index not initialized; skipping save")
            return

        try:
            _ensure_index_dir()
            faiss.write_index(_index, str(INDEX_FILE))
            _atomic_write_json(META_FILE, _meta)
            logger.info(
                "Saved FAISS index and metadata (vectors=%d)",
                len(_meta.get("ids", [])),  # type: ignore[arg-type]
            )
        except Exception:
            logger.exception("Failed to save FAISS index or metadata")


# --------- chunking (naive but improved) ---------
def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> List[str]:
    """
    Chunk text with a bias toward splitting at newlines or punctuation boundaries.
    Keeps `overlap` characters between chunks to preserve local context.

    This function is part of the public API and is used by scripts.ingest_repo.
    """
    if not text:
        return []

    text = text.strip()
    L = len(text)
    chunks: List[str] = []
    start = 0

    while start < L:
        end = min(start + chunk_size, L)
        window = text[start:end]

        # try to break at the last "nice" boundary before `end`
        sep_pos = max(
            window.rfind("\n"),
            window.rfind(". "),
            window.rfind("; "),
            window.rfind(")"),
        )

        if sep_pos > int(chunk_size * 0.5):
            cut = start + sep_pos + 1
        else:
            cut = end

        chunk = text[start:cut].strip()
        if chunk:
            chunks.append(chunk)

        if cut >= L:
            break

        # keep overlap but never move backwards
        start = max(cut - overlap, cut)

    return chunks


# --------- public API: add_documents ---------
def add_documents(docs: List[Tuple[str, str]]) -> int:
    """
    Add a list of (source_id, text) documents to the FAISS index.

    Returns:
        Number of chunks actually added.

    Raises:
        RuntimeError if faiss is not available or embedding provider fails critically.
    """
    global _index, _meta

    if faiss is None:
        raise RuntimeError("faiss not installed; cannot add documents")

    _load_index()

    to_embed: List[str] = []
    metas: List[Tuple[str, Dict[str, str]]] = []

    # Build chunks + metadata, deduplicating by (source + chunk sha)
    existing_meta: Dict[str, Dict[str, str]] = _meta.get("meta", {})  # type: ignore[assignment]

    for source, text in docs:
        for chunk in chunk_text(text):
            chunk_sha = hashlib.sha1(chunk.encode("utf-8")).hexdigest()
            chunk_id = hashlib.sha1(
                (str(source) + chunk_sha).encode("utf-8")
            ).hexdigest()

            if chunk_id in existing_meta:
                continue  # skip duplicates

            metas.append(
                (chunk_id, {"source": str(source), "text": chunk, "sha": chunk_sha})
            )
            to_embed.append(chunk)

    if not to_embed:
        logger.debug("add_documents: nothing to add (all duplicates or no content)")
        return 0

    # Embed text chunks
    try:
        vectors = embed_texts(to_embed)
    except Exception as e:
        logger.exception("Embedding service failed in add_documents: %s", e)
        raise

    if not vectors:
        logger.warning(
            "add_documents: embed_texts returned no vectors; aborting indexing."
        )
        return 0

    # Normalize and add to index
    try:
        vecs = _normalize(vectors)
    except Exception as e:
        logger.exception("Normalization failed in add_documents: %s", e)
        raise

    with _state_lock:
        if _index is None:
            _index = _create_index()

        try:
            _index.add(vecs)
        except Exception:
            logger.exception("Failed to add vectors to FAISS index")
            raise

        # update metadata
        ids_list: List[str] = _meta.setdefault("ids", [])  # type: ignore[assignment]
        meta_map: Dict[str, Dict[str, str]] = _meta.setdefault(
            "meta", {}
        )  # type: ignore[assignment]

        for cid, m in metas:
            ids_list.append(cid)
            meta_map[cid] = m

        # persist to disk
        _save_index()

    logger.info(
        "add_documents: added %d chunks (total=%d)",
        len(metas),
        len(_meta.get("ids", [])),  # type: ignore[arg-type]
    )
    return len(metas)


# --------- public API: query ---------
def query(text: str, top_k: int = 6) -> List[dict]:
    """
    Query the FAISS index and return a list of matches:

        [{ "source": str, "text": str, "score": float }, ...]

    Behavior:
      - If faiss is not available or index is empty → returns [].
      - Errors in embedding / search are logged and degrade to [].
    """
    global _index, _meta

    if faiss is None:
        logger.debug("query: faiss not available; returning [].")
        return []

    if not text:
        return []

    _load_index()
    if _index is None:
        logger.debug("query: index not initialized; returning [].")
        return []

    # Embed query
    try:
        vectors = embed_texts([text])
    except Exception:
        logger.exception("Embedding service failed during query()")
        return []

    if not vectors:
        logger.debug("query: embed_texts returned empty; returning [].")
        return []

    # Normalize
    try:
        qv = _normalize(vectors)
    except Exception:
        logger.exception("query: normalization failed")
        return []

    # Search
    with _state_lock:
        try:
            D, I = _index.search(qv, top_k)  # type: ignore[call-arg]
        except Exception:
            logger.exception("FAISS search failed in query()")
            return []

        results: List[dict] = []
        ids: List[str] = _meta.get("ids", [])  # type: ignore[assignment]
        meta_map: Dict[str, Dict[str, str]] = _meta.get("meta", {})  # type: ignore[assignment]

        # D, I are numpy arrays (shape: 1 x top_k)
        for dist, idx in zip(D[0].tolist(), I[0].tolist()):
            if idx < 0 or idx >= len(ids):
                continue
            cid = ids[idx]
            m = meta_map.get(cid, {})
            results.append(
                {
                    "source": m.get("source"),
                    "text": m.get("text"),
                    "score": float(dist),
                }
            )

    return results
