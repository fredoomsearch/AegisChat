# scripts/ingest_repo.py
"""
Repository ingestion script for AEGIS retrieval.

Scans a source tree, collects text/code files, and pushes them into
the FAISS-backed retrieval index via app.services.retrieval_service.add_documents.

Environment:
  - INGEST_ROOT  (default: ".")
  - INGEST_BATCH (default: "40")

Behavior (unchanged conceptually):
  - Walks ROOT
  - Skips some dirs (venv, .venv, .git, data, __pycache__)
  - Indexes a set of extensions (.py, .md, .txt, .dockerfile, .tf, .yml, .yaml, .json, Dockerfile)
  - Uses add_documents in batches
"""

from __future__ import annotations

import logging
import os
import sys
from typing import List, Tuple

from cairo import Path

from app.services.retrieval_service import add_documents

# ------------------------------------------------------
# Logging
# ------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="[ingest] %(message)s")
log = logging.getLogger("ingest")

# ------------------------------------------------------
# Configuration
# ------------------------------------------------------
ROOT = os.path.abspath(os.getenv("INGEST_ROOT", "."))
EXTS = {
    ".py",
    ".md",
    ".txt",
    ".dockerfile",
    ".tf",
    ".yml",
    ".yaml",
    ".json",
}
SKIP_DIRS = {
    "venv",
    ".venv",
    ".git",
    "data",
    "__pycache__",
}

BATCH = int(os.getenv("INGEST_BATCH", "40"))
MAX_FILE_SIZE = 2_000_000  # 2MB safety cap for text files (you can raise it)


def main():
    docs = []
    base = Path(".")
    for path in base.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        docs.append((str(path), text))

    added = add_documents(docs)
    print(f"Added {added} chunks to FAISS index.")

if __name__ == "__main__":
    main()


# ------------------------------------------------------
# Helpers
# ------------------------------------------------------
def should_skip_dir(path: str) -> bool:
    """Return True if this directory (or any of its components) should be skipped."""
    parts = path.replace("\\", "/").split("/")
    return any(p in SKIP_DIRS for p in parts)


def is_valid_file(root: str, filename: str) -> bool:
    """
    Decide whether a file should be ingested based on its extension/name.
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext in EXTS:
        return True

    # Special-case Dockerfile (no extension)
    if filename.lower() == "dockerfile":
        return True

    return False


def safe_read(filepath: str) -> str | None:
    """
    Safely read a file as UTF-8 text.

    - Skips too large files (> MAX_FILE_SIZE)
    - Skips binary-like / non-UTF-8 files
    - Returns None on any failure instead of raising
    """
    try:
        size = os.path.getsize(filepath)
        if size > MAX_FILE_SIZE:
            log.warning(f"Skipping large file (>2MB): {filepath}")
            return None

        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        log.warning(f"Skipping binary-like file: {filepath}")
        return None
    except Exception as exc:
        log.warning(f"Error reading {filepath}: {exc}")
        return None


def collect_docs(root: str) -> List[Tuple[str, str]]:
    """
    Walk filesystem under `root` and collect (relative_path, text) docs.
    """
    docs: List[Tuple[str, str]] = []

    log.info(f"Scanning root: {root}")
    for dirpath, dirs, files in os.walk(root):
        if should_skip_dir(dirpath):
            continue

        for fname in files:
            if not is_valid_file(dirpath, fname):
                continue

            fpath = os.path.join(dirpath, fname)
            text = safe_read(fpath)
            if text is None:
                continue

            # relative path to keep index stable
            rel = os.path.relpath(fpath, root).replace("\\", "/")
            docs.append((rel, text))

    log.info(f"Collected {len(docs)} files to index.")
    return docs


def ingest_docs(docs: List[Tuple[str, str]]) -> None:
    """
    Send docs to the retrieval index in batches using add_documents.
    """
    if not docs:
        log.info("No documents found. Exiting.")
        return

    for idx in range(0, len(docs), BATCH):
        batch = docs[idx : idx + BATCH]
        batch_id = idx // BATCH
        try:
            n = add_documents(batch)
            log.info(f"Batch {batch_id}: added {n} chunks")
        except Exception as e:
            # Log and keep going, do not kill ingestion
            log.exception(f"Batch {batch_id} failed: {e}")


# ------------------------------------------------------
# Main script entry
# ------------------------------------------------------
if __name__ == "__main__":
    try:
        documents = collect_docs(ROOT)
        ingest_docs(documents)
    except KeyboardInterrupt:
        log.info("Interrupted by user (Ctrl+C). Exiting.")
        sys.exit(1)
