# 🛡️ AegisChat

AegisChat is a modular AI assistant built on top of **FastAPI**, a **local LLM** (WizardCoder via `llama.cpp`), optional **OpenAI fallback**, **web tools**, and **long-term memory** backed by both JSON files and SQL (SQLite/Postgres) storage.

It is designed as a “workbench” for experimentation:

* Local coding assistant
* Web-augmented Q&A
* Retrieval-Augmented Generation (RAG) over your own repos
* Playground for prompt / tools / memory research

---

## ✨ Features

* **Local LLM**

  * WizardCoder 7B (`.gguf`) through `llama.cpp`
  * Optional OpenAI/remote model fallback via environment variables

* **Chat with Memory**

  * Per-conversation history persisted in SQLite (or any SQLAlchemy-compatible DB)
  * JSON-based fallback memory if DB is not available
  * Summarization to keep context windows under control

* **Web & Tools**

  * `GET /websearch` endpoint to hit a configurable web search backend
  * `POST /fetch_url` to fetch and sanitize arbitrary URLs
  * Optional `.onion` support via Tor
  * Simple heuristics for “needs web search?” vs “offline answer”

* **RAG / Retrieval**

  * FAISS vector index for code/docs
  * `scripts/ingest_repo.py` to index a repo or directory
  * `app/services/retrieval_service.py` for query-time retrieval

* **Image → Text Context (local OCR)**

  * `POST /analyze_image` endpoint
  * Uses Tesseract + `pytesseract` (no API key required)
  * Extracted text is fed back into the chat as additional context

* **UI**

  * Single-page HTML/JS chat client in `ui/index.html`
  * Modes for:

    * Normal chat
    * Web-search chat
    * URL-reader
    * Image analysis (via OCR)
  * Conversation ID management in `localStorage` (export/import)

* **Ops**

  * Dockerfile + `docker-compose.yml`
  * Nginx reverse proxy config
  * Prometheus metrics config
  * GitHub Actions CI (`.github/workflows/ci.yml`)

---

## 🧱 High-Level Architecture

* **Frontend (UI)**
  Static HTML/JS served by FastAPI (`/` serving `ui/index.html`).

* **API Layer** – `app/api.py`

  * `GET /websearch` – structured web search
  * `POST /fetch_url` – fetch & sanitize URL (clearnet/Tor)
  * `POST /chat` – main chat endpoint (LLM + memory + tools)
  * `POST /analyze_image` – image → OCR text (local, no API key)

* **LLM Layer**

  * `app/llm_client_offload.py`

    * Calls local `llama.cpp` or remote model
    * Offloads heavy inference to a `ThreadPoolExecutor` (non-blocking event loop)
  * `app/llm_utils.py`

    * Prompt building
    * Context trimming
    * Code-question detection
    * Language detection helpers

* **Memory Layer**

  * `app/memory.py`

    * JSON conversations
    * Simple summarization
  * `app/db/*` – SQLAlchemy models & CRUD for Conversation/Message/Embeddings:

    * `base.py` – engine, `Base`, `init_db`
    * `models.py` – ORM models
    * `crud_conversation.py` – conversation/message CRUD
    * `crud_summary.py` – summary CRUD
    * `deps.py` – FastAPI DB dependency

* **Tools Layer** – `app/tools.py`

  * `web_search_structured` – web search adapter
  * `fetch_url` / `fetch_url_via_tor` – HTTP fetching
  * `sanitize_web_context` – cleans and truncates HTML/text

* **Retrieval Layer** – `app/services/`

  * `embedding_service.py` – embeddings provider (local first, OpenAI fallback)
  * `retrieval_service.py` – FAISS index builder/query
  * `conversation_services.py` – high-level conversation orchestration over DB + retrieval

* **Vision/OCR Layer** – `app/vision_client.py`

  * `analyze_image_bytes` – uses Tesseract (`pytesseract`) to:

    * open image
    * run OCR (Spanish + English)
    * build a user-friendly description that the LLM can consume as context

---

## 📂 Project Structure

### Root

* `main.py` – FastAPI app entrypoint; mounts router and serves UI.
* `.env` – Environment configuration.
* `aegis.db` – Default SQLite DB (created at runtime).
* `docker-compose.yml` – Multi-service setup (app + Nginx + optional extras).
* `Dockerfile` – Container image definition.
* `.dockerignore` – Build context ignore list.
* `pyproject.toml` – Project metadata (tooling, formatting, etc.).
* `requirements.txt` – Python dependencies.
* `sqlschemas.sql` – Optional schema reference or migrations.
* `.pre-commit-config.yaml` – Local lint/format hooks.
* `README.md` – This file.

### `.github/workflows/`

* `ci.yml` – CI pipeline (tests, lint, build).

### `monitoring/`

* `prometheus.yml` – Prometheus scrape configuration.

### `nginx/`

* `nginx.conf` – Reverse proxy rules, caching, compression, static serving.

### `ui/`

* `index.html` – Main chat UI; contains:

  * Chat bubbles rendering
  * Markdown + sanitization (`marked` + `DOMPurify`)
  * Mode toggles (chat/web/URL/image)
  * Conversation ID export/import
  * Language toggle (ES/EN)
* `*.png` / `*.ico` – Logos and icons.

### `models/`

* `wizardcoder-python-7b-v1.0.Q5_K_M.gguf` – Local quantized LLM used by `llama.cpp`.

### `app/`

* `__init__.py`
* `config.py` – Loads env vars, paths, model configs, DB URL, etc.
* `schemas.py` – Pydantic models (`ChatRequest`, `ChatResponse`, etc.).
* `api.py` – All HTTP endpoints (`/chat`, `/websearch`, `/fetch_url`, `/analyze_image`).
* `tools.py` – Web search, Tor, URL fetching, sanitization.
* `memory.py` – JSON-based conversation storage and basic summarization.
* `llm_utils.py` – System prompts, message building, context trimming, language detection helpers.
* `llm_client_offload.py` – LLM invocation logic, local model loading, threadpool offload.
* `llm_client.py` – Legacy/alternative client wrapper (optional).
* `vision_client.py` – OCR-based image analysis (local, no external API).

### `app/services/`

* `embedding_service.py` – Unify embedding creation (local vs OpenAI).
* `retrieval_service.py` – Manage FAISS index (build, save, load, query).
* `conversation_services.py` – High-level chat orchestration with DB-backed history.

### `app/db/`

* `base.py` – SQLAlchemy engine + metadata + `init_db()`.
* `models.py` – ORM models (Conversation, Message, Embedding, Summary).
* `deps.py` – FastAPI dependency for DB session.
* `crud_conversation.py` – CRUD operations for conversations & messages.
* `crud_summary.py` – CRUD for summary records.

### `scripts/`

* `ingest_repo.py` – CLI script to index a repository/directory into FAISS:

  * reads files
  * chunks content
  * generates embeddings
  * stores them in the vector index

---

## ⚙️ Configuration

All configuration is driven by environment variables (usually loaded from `.env` via `python-dotenv` or similar).

Typical variables:

```env
# LLM / model
AEGIS_MODEL_PATH=./models/wizardcoder-python-7b-v1.0.Q5_K_M.gguf
AEGIS_N_THREADS=4
AEGIS_N_CTX=2048
AEGIS_N_GPU_LAYERS=0

# Database
DATABASE_URL=sqlite:///./aegis.db
# or e.g.: postgresql+psycopg2://user:pass@host:port/dbname

# Optional Remote LLM / OpenAI
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini

# Web search configuration
WEBSEARCH_ENDPOINT=
WEBSEARCH_API_KEY=

# Other flags (depending on implementation)
AEGIS_USE_OPENAI_FALLBACK=false
```

> If `OPENAI_API_KEY` is empty, AegisChat runs fully local (WizardCoder + OCR).

---

## 🚀 Running Locally

### 1. Create virtualenv & install deps

```bash
python -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Install system packages for OCR (Ubuntu)

```bash
sudo apt update
sudo apt install -y tesseract-ocr tesseract-ocr-spa tesseract-ocr-eng
```

### 3. Configure `.env`

Create a `.env` file at the project root and set the variables you need (model path, DB URL, etc.).

### 4. Initialize DB (optional, auto-created)

```bash
python -m app.db.base
# or: run main.py once, which calls init_db() on startup
```

### 5. Run the server

```bash
uvicorn main:app --reload
```

Then open:

```text
http://127.0.0.1:8000/
```

* The UI is served from `/` → `ui/index.html`.
* The API is available under the same host (e.g. `/chat`, `/websearch`, `/fetch_url`, `/analyze_image`).

---

## 🔌 HTTP API Overview

### `POST /chat`

Main chat endpoint.

**Request (JSON)**

```json
{
  "message": "Explain this Python error",
  "conversation_id": "optional-existing-id-or-null",
  "lang": "es"
}
```

**Response**

```json
{
  "response": "LLM answer here...",
  "conversation_id": "a5b70034-396a-4faf-8c47-08edf0e5672d"
}
```

**Special prefixes:**

* `"web:..."` → force web search + RAG
* `"url:..."` → treat message as URL for `/fetch_url` + summarization
* `"analyze:..."` → analysis mode prompt (explain this text / code step-by-step)

---

### `GET /websearch`

Wrapper around `web_search_structured`.

**Query params:**

* `q` – search query (required)
* `n` – number of results (default 3, max 10)

**Response:** list of objects

```json
[
  {
    "title": "...",
    "snippet": "...",
    "link": "https://..."
  }
]
```

---

### `POST /fetch_url`

Fetches a URL and returns cleaned text.

**Request**

```json
{
  "url": "https://example.com"
}
```

**Response**

```json
{
  "status": 200,
  "text": "Cleaned page text..."
}
```

Supports `.onion` URLs via Tor if configured.

---

### `POST /analyze_image`

Uploads an image and returns extracted text / simple description (OCR, no API key).

**Multipart form fields**

* `file` – image file
* `lang` – `"es"` or `"en"` (default: `"es"`)

**Response**

````json
{
  "description": "He extraído el siguiente texto de la imagen...\n```text\n...OCR output...\n```"
}
````

The frontend then sends this description back into `/chat` as:

```text
Context from image:
<description>
```

so the LLM can use it as context.

---

## 🧪 Development Tips

* **Prompt tuning** – change system messages and context strategy in `app/llm_utils.py`.
* **Model behavior** – adjust `max_new_tokens`, temperature, threads, `n_ctx` in `app/llm_client_offload.py`.
* **Memory strategy** – modify summarization and retention logic in:

  * `app/services/conversation_services.py`
  * `app/memory.py`
* **Adding new tools** – extend `app/tools.py` and call them from `app/api.py` inside the `/chat` pipeline.
* **New retrieval corpora** – use `scripts/ingest_repo.py` or create a similar script that pushes embeddings into `retrieval_service`.

---

## 📌 Roadmap Ideas

Some natural extensions for AegisChat:

* Multi-model routing (code model vs general model)
* Per-user profiles and auth
* Fine-grained tool routing (only call web/URL/DB tools when strictly needed)
* More sophisticated RAG (source re-ranking, citations in answers)
* Streaming responses (Server-Sent Events or WebSockets)
* UI improvements (multi-tab conversations, RAG visualizations)

---

## 📜 License

*Add your license here (e.g. MIT, Apache-2.0, Julian Grajales,.).*
