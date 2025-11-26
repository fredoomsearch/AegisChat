"""
Improved tools module for web search / fetching.

Public API (unchanged):
- find_brave_binary()
- web_search_structured()
- web_search()
- duckduckgo_search_html_structured()
- browser_search_scrape_structured()
- fetch_url()
- is_onion()
- fetch_url_via_tor()
- sanitize_web_context()

Features:
- structured logging
- requests.Session reuse + retry/backoff
- configurable timeouts and constants via env vars
- safer subprocess handling
- clearer typing and docstrings
- Selenium/chromedriver optional (non-fatal)
"""
from __future__ import annotations

import getpass
import logging
import os
import re
import shutil
import signal
import subprocess
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------
# Optional Selenium imports (non-fatal if missing)
# ---------------------------------------------------------------------
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.common.by import By

    SELENIUM_AVAILABLE = True
except Exception:
    SELENIUM_AVAILABLE = False

# chromedriver_autoinstaller optional
try:
    import chromedriver_autoinstaller

    CHROMEDRIVER_AUTOINSTALLER_AVAILABLE = True
except Exception:
    CHROMEDRIVER_AUTOINSTALLER_AVAILABLE = False

# ---------------------------------------------------------------------
# Logger configuration
# ---------------------------------------------------------------------
logger = logging.getLogger("app.tools")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(os.getenv("TOOLS_LOG_LEVEL", "INFO"))

# ---------------------------------------------------------------------
# Constants & defaults
# ---------------------------------------------------------------------
DEFAULT_USER_AGENT = "Mozilla/5.0 (AegisChat)"
REQUEST_TIMEOUT = int(os.getenv("TOOLS_REQUEST_TIMEOUT", "12"))  # seconds
TOR_SOCKS_PROXY = os.getenv("TOR_SOCKS_PROXY", "socks5h://127.0.0.1:9050")
FETCH_TIMEOUT = int(os.getenv("TOOLS_FETCH_TIMEOUT", "15"))
MAX_BYTES = int(os.getenv("TOOLS_MAX_BYTES", str(200_000)))
DUCKDUCKGO_HTML = "https://html.duckduckgo.com/html"

# Some common browser binary locations to probe
COMMON_BRAVE_PATHS = [
    "/usr/bin/brave-browser",
    "/usr/bin/brave",
    "/snap/bin/brave",
    shutil.which("brave-browser") or "",
    shutil.which("brave") or "",
    shutil.which("google-chrome") or "",
    shutil.which("chromium-browser") or "",
    shutil.which("chromium") or "",
]


# ---------------------------------------------------------------------
# Requests session with retries
# ---------------------------------------------------------------------
def _create_session(
    retries: int = 2,
    backoff_factor: float = 0.3,
    status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504),
) -> requests.Session:
    """
    Create a requests.Session with retry/backoff and a default User-Agent.
    """
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=frozenset(["GET", "POST"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    return session


# default shared session (can be overridden for testing)
_default_session: requests.Session = _create_session()


# ---------------------------------------------------------------------
# Process utilities
# ---------------------------------------------------------------------
def _safe_pkill(pattern: str) -> None:
    """
    Best-effort: find processes matching `pattern` owned by current user and SIGTERM them.
    Quietly ignores errors. Retains original behavior but logs actions.
    """
    try:
        user = getpass.getuser()
    except Exception:
        user = None

    # Try pgrep path first (more controlled)
    try:
        if user:
            out = subprocess.run(
                ["pgrep", "-u", user, "-f", pattern],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            pids: List[int] = []
            if out.stdout:
                for line in out.stdout.splitlines():
                    try:
                        pids.append(int(line.strip()))
                    except Exception:
                        continue
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                    logger.debug("SIGTERM sent to pid %s matching %s", pid, pattern)
                except Exception:
                    logger.debug("Failed to kill pid %s", pid)
            return
    except Exception:
        logger.debug("pgrep path failed for pattern=%s", pattern)

    # Fallback to pkill (still best-effort)
    try:
        subprocess.run(
            ["pkill", "-u", user or getpass.getuser(), "-f", pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        logger.debug("pkill fallback failed for pattern=%s", pattern)


# ---------------------------------------------------------------------
# Brave / Chromium binary detection
# ---------------------------------------------------------------------
def find_brave_binary() -> Optional[str]:
    """
    Returns first executable path for Brave/Chrome/Chromium or None.
    """
    for path in COMMON_BRAVE_PATHS:
        if not path:
            continue
        try:
            if os.path.isfile(path) and os.access(path, os.X_OK):
                logger.debug("Found browser binary at %s", path)
                return path
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------
# DuckDuckGo HTML scraper (primary)
# ---------------------------------------------------------------------
def duckduckgo_search_html_structured(
    query: str,
    max_results: int = 3,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, str]]:
    """
    Query https://html.duckduckgo.com/html using requests + BeautifulSoup.
    Returns a list of dicts: [{'title','snippet','link'}, ...]
    Non-fatal: returns [] on any error (keeps original API).
    """
    session = session or _default_session
    try:
        headers = {"User-Agent": session.headers.get("User-Agent", DEFAULT_USER_AGENT)}
        resp = session.post(
            DUCKDUCKGO_HTML,
            data={"q": query},
            timeout=REQUEST_TIMEOUT,
            headers=headers,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results: List[Dict[str, str]] = []

        for div in soup.select("div.result")[:max_results]:
            a = div.select_one("a")
            title = a.get_text(strip=True) if a else ""
            link = a.get("href") if a and a.has_attr("href") else ""
            snippet_el = div.select_one(".result__snippet, .snippet, p")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            results.append({"title": title, "snippet": snippet, "link": link})

        logger.debug("duckduckgo returned %d results for query=%s", len(results), query)
        return results
    except Exception as exc:
        logger.debug("duckduckgo_search_html_structured failed: %s", exc)
        return []


# ---------------------------------------------------------------------
# Selenium fallback (optional)
# ---------------------------------------------------------------------
def browser_search_scrape_structured(
    query: str,
    max_results: int = 3,
    headless: bool = True,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, str]]:
    """
    Try Selenium + Brave (chromedriver autoinstaller). If unavailable or any issue occurs,
    falls back to duckduckgo_search_html_structured.
    """
    session = session or _default_session

    # quick fallbacks if selenium not present
    if not SELENIUM_AVAILABLE or not CHROMEDRIVER_AUTOINSTALLER_AVAILABLE:
        logger.debug(
            "Selenium or chromedriver autoinstaller not available; using duckduckgo."
        )
        return duckduckgo_search_html_structured(query, max_results, session=session)

    brave_bin = find_brave_binary()
    if not brave_bin:
        logger.debug("Brave/Chromium binary not found; using duckduckgo.")
        return duckduckgo_search_html_structured(query, max_results, session=session)

    try:
        chromedriver_path = chromedriver_autoinstaller.install()
    except Exception as exc:
        logger.debug("chromedriver_autoinstaller failed: %s", exc)
        return duckduckgo_search_html_structured(query, max_results, session=session)

    # Prepare options
    try:
        options = ChromeOptions()
        options.binary_location = brave_bin
        if headless:
            try:
                options.add_argument("--headless=new")
            except Exception:
                options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument(
            f"user-agent={session.headers.get('User-Agent', DEFAULT_USER_AGENT)}"
        )
    except Exception as exc:
        logger.debug("Failed to prepare ChromeOptions: %s", exc)
        return duckduckgo_search_html_structured(query, max_results, session=session)

    # Ensure driver executable has exec bit
    try:
        if chromedriver_path and not os.access(chromedriver_path, os.X_OK):
            try:
                os.chmod(chromedriver_path, 0o755)
            except Exception:
                logger.debug("Could not chmod chromedriver at %s", chromedriver_path)
    except Exception:
        pass

    driver = None
    try:
        service = ChromeService(executable_path=chromedriver_path)
        driver = webdriver.Chrome(service=service, options=options)

        url = f"{DUCKDUCKGO_HTML}?q={requests.utils.quote(query)}"
        driver.get(url)
        # short sleep to allow JS rendering if any; keep minimal
        time.sleep(1.0)

        results: List[Dict[str, str]] = []
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, "div.result")
        except Exception:
            elements = []

        for el in elements[:max_results]:
            try:
                a = el.find_element(By.CSS_SELECTOR, "a")
                title = a.text.strip()
                link = a.get_attribute("href")
                snippet = ""
                try:
                    snippet_el = el.find_element(By.CSS_SELECTOR, ".result__snippet")
                    snippet = snippet_el.text.strip()
                except Exception:
                    snippet = ""
                results.append({"title": title, "snippet": snippet, "link": link})
            except Exception:
                continue

        if not results:
            return duckduckgo_search_html_structured(
                query, max_results, session=session
            )
        return results
    except Exception as exc:
        logger.debug("browser_search_scrape_structured failed: %s", exc)
        return duckduckgo_search_html_structured(query, max_results, session=session)
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass
        _safe_pkill("chromedriver")
        _safe_pkill("brave")


# ---------------------------------------------------------------------
# Public API: single function to call
# ---------------------------------------------------------------------
def web_search_structured(
    query: str,
    num_results: int = 3,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, str]]:
    """
    Public programmatic API: returns list of {'title','snippet','link'}.
    Tries DuckDuckGo HTML first, then Selenium browser scrape fallback.
    """
    session = session or _default_session

    # 1) primary: DuckDuckGo HTML
    results = duckduckgo_search_html_structured(query, num_results, session=session)
    if results:
        return results

    # 2) fallback: Selenium
    try:
        fallback_results = browser_search_scrape_structured(
            query, max_results=num_results, headless=True, session=session
        )
        if fallback_results:
            return fallback_results
    except Exception as exc:
        logger.debug("web_search_structured fallback failed: %s", exc)

    return []


def web_search(
    query: str,
    num_results: int = 3,
    session: Optional[requests.Session] = None,
) -> str:
    """
    String wrapper for easier debugging/CLI usage (preserves original behavior).
    """
    results = web_search_structured(query, num_results, session=session)
    if not results:
        return f"No results for: {query}"

    blocks: List[str] = []
    for r in results:
        blocks.append(
            f"{r.get('title', '')}\n{r.get('snippet', '')}\n{r.get('link', '')}"
        )
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------
# Fetch URL as cleaned text
# ---------------------------------------------------------------------
def fetch_url(
    url: str, max_chars: int = 4000, session: Optional[requests.Session] = None
) -> Dict[str, Any]:
    """
    Fetch a clearnet URL and return a dict with:
      - ok: bool
      - status: HTTP status code (if available)
      - text: cleaned text content (if ok)
      - error: error message (if not ok)
    """
    session = session or _default_session
    headers = {"User-Agent": session.headers.get("User-Agent", DEFAULT_USER_AGENT)}

    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, headers=headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        text = " ".join(soup.get_text(separator="\n").split())
        if len(text) > max_chars:
            text = text[:max_chars] + " … [truncated]"
        return {
            "ok": True,
            "status": resp.status_code,
            "text": f"Content from {url}:\n{text}",
        }
    except Exception as exc:
        logger.debug("fetch_url failure for %s: %s", url, exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------
# Tor fetch support (for .onion)
# ---------------------------------------------------------------------
def is_onion(url: str) -> bool:
    """
    Returns True if the URL is a .onion host, False otherwise.
    """
    try:
        host = urlparse(url).hostname or ""
        return host.lower().endswith(".onion")
    except Exception:
        return False


def fetch_url_via_tor(
    url: str,
    tor_proxy: str = TOR_SOCKS_PROXY,
    max_bytes: int = MAX_BYTES,
) -> Dict[str, Any]:
    """
    Fetch URL using Tor SOCKS proxy. Returns dict: {ok, status, text, error}

    Requires:
      - tor daemon listening on `tor_proxy`
      - requests[socks] installed
    """
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    proxies = {"http": tor_proxy, "https": tor_proxy}

    try:
        with requests.get(
            url,
            headers=headers,
            timeout=FETCH_TIMEOUT,
            stream=True,
            proxies=proxies,
        ) as r:
            r.raise_for_status()
            data: List[bytes] = []
            total = 0
            for chunk in r.iter_content(chunk_size=8192):
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    # keep within max_bytes
                    data.append(chunk[: max_bytes - (total - len(chunk))])
                    break
                data.append(chunk)

            text = b"".join(data).decode(errors="replace")
            # strip potentially dangerous tags (simple heuristic)
            text = re.sub(r"(?is)<script.*?>.*?</script>", "", text)
            text = re.sub(r"(?is)<iframe.*?>.*?</iframe>", "", text)
            return {"ok": True, "status": r.status_code, "text": text}
    except RequestException as exc:
        logger.debug("fetch_url_via_tor request exception: %s", exc)
        return {
            "ok": False,
            "status": getattr(exc.response, "status_code", None),
            "error": str(exc),
        }
    except Exception as exc:
        logger.debug("fetch_url_via_tor generic exception: %s", exc)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------
# Web context sanitizer (reduce date hallucination)
# ---------------------------------------------------------------------
_date_pattern = re.compile(
    r'(\b(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?),?\s+\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b)|'
    r'(\b\d{4}-\d{2}-\d{2}\b)|'
    r'(\b\d{1,2}/\d{1,2}/\d{2,4}\b)',
    flags=re.IGNORECASE,
)


def sanitize_web_context(text: str, max_chars: int = 1200) -> str:
    """
    Shorten the web context and add a warning if obvious old years are present.
    Keeps output safe for model instruction injection.
    """
    if not text:
        return ""

    # Detect potentially outdated years
    years = re.findall(r"\b(19\d{2}|20\d{2}|21\d{2})\b", text)
    flagged: List[int] = []
    for y in years:
        try:
            yi = int(y)
            if yi < 2023:
                flagged.append(yi)
        except Exception:
            continue

    clean = text[:max_chars]

    if flagged:
        clean = (
            "⚠️ Advertencia: La(s) fuente(s) web contienen años potencialmente desactualizados "
            f"{sorted(set(flagged))}. No asumir que son actuales.\n\n"
        ) + clean

    # Remove script tags and normalize whitespace
    clean = re.sub(r"(?is)<script.*?>.*?</script>", "", clean)
    clean = re.sub(r"\s{2,}", " ", clean)
    return clean


def _sanitize_messages(messages: List[Dict]) -> List[Dict]:
    """
    Limita el tamaño de los mensajes de usuario para evitar context overflow.
    - Si un mensaje de usuario > 3000 chars, recortamos cabeza + cola y añadimos marcador.
    - No cambia el API, solo limpia contenido excesivo.
    """
    MAX_USER_CHARS = 3000
    HEAD = 1500
    TAIL = 800

    cleaned: List[Dict] = []
    for m in messages:
        content = (m.get("content") or "")
        role = m.get("role", "user")
        if role == "user" and len(content) > MAX_USER_CHARS:
            head = content[:HEAD]
            tail = content[-TAIL:]
            content = (
                head
                + "\n\n[... contenido recortado por longitud ...]\n\n"
                + tail
            )
        cleaned.append({**m, "content": content})
    return cleaned
