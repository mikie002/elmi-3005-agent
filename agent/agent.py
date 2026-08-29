"""
Pure-Python agent for Global AI Construct - Lab 01 (single agent).

No langchain, no compiled extensions - just the Python standard library
plus one tiny pure-Python dependency (python-dotenv). Works even on
machines with Smart App Control that block pip-compiled DLLs.

eLmi 3005 - turbo edition: 20 tools (research + memory + read-only coding
helpers + gated REAL terminal execution with persistent working dir),
conversation memory, persistent notes (remember/recall), live tool-call
traces, lenient JSON parsing, per-tool timeouts, and tool-result caps.

The agent:
  1. Sends the user question + tool descriptions to an OpenRouter model
  2. Receives a list of tool calls (function-calling format)
  3. Executes each tool locally (every HTTP tool has a timeout)
  4. Feeds results back to the model
  5. Repeats until the model stops requesting tools, then returns the answer
"""

import gzip
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
import urllib.error
import urllib.parse
import urllib.request
from dotenv import load_dotenv

# Ensure the console can print Unicode (the model often returns arrows/emoji)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

MODEL = os.getenv("ELMI_MODEL", "minimax/minimax-m3:free")
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_STEPS = 10
TOOL_TIMEOUT = 20          # seconds per HTTP tool call
RESULT_LIMIT = 4000        # max characters of a tool result sent back to the model
_MEMORY_FILE = os.path.join(os.path.dirname(__file__), "memory.json")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


# ---------------------------------------------------------------------------
# HTTP helper (gzip-aware, timed out)
# ---------------------------------------------------------------------------
def _http_get(url: str, timeout: int = TOOL_TIMEOUT, headers=None) -> str:
    hdrs = {"User-Agent": _UA}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "ignore")


def _clean(html_frag: str) -> str:
    text = re.sub(r"<[^>]+>", "", html_frag)
    for k, v in [
        ("&quot;", '"'), ("&amp;", "&"), ("&#x27;", "'"), ("&nbsp;", " "),
        ("&lt;", "<"), ("&gt;", ">"), ("&mdash;", "-"), ("&ndash;", "-"),
        ("&#241;", "ñ"), ("&#39;", "'"),
    ]:
        text = text.replace(k, v)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def search_web(query: str) -> str:
    """Searches the internet for current information about any topic.
    Use this when the question needs recent news, facts, or anything you
    cannot know from memory."""
    try:
        result = _bing(query)
        if not result or len(result) < 10:
            return "Search returned no results. Try rephrasing your query."
        return result
    except Exception as exc:
        return f"Search failed: {str(exc)}. Try a different query."


def open_url(url: str) -> str:
    """Fetches a web page and returns its title and readable text.
    Use this to read an article, a page, or an API endpoint."""
    try:
        html = _http_get(url)
        title_m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        body = re.sub(r"(?is)<(script|style|noscript|svg|head).*?</\1>", " ", html)
        body = _clean(re.sub(r"<[^>]+>", "\n", body))
        title = _clean(title_m.group(1)) if title_m else "no title"
        return f"Title: {title}\n\n" + body[:RESULT_LIMIT]
    except Exception as exc:
        return f"Could not fetch {url}: {str(exc)}"


def get_weather(city: str) -> str:
    """Returns the current weather for the given city."""
    try:
        text = _http_get("https://wttr.in/" + urllib.parse.quote(city) + "?format=3",
                         headers={"User-Agent": "curl/8.0"})
        return text.strip() or f"No weather data for {city}."
    except Exception as exc:
        return f"Weather lookup failed: {str(exc)}"


def get_quote(symbol: str) -> str:
    """Returns the latest price for a stock, crypto, index, or currency pair.
    Examples: AAPL, TSLA, BTC-USD, ETH-USD, ^GSPC (S&P 500), EURUSD=X."""
    try:
        sym = symbol.strip().upper()
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}"
        data = json.loads(_http_get(url,
                                    headers={"User-Agent": _UA.replace("Chrome/120.0",
                                                                        "Chrome/131.0")}))
        result = (data.get("chart", {}).get("result") or [])[0]
        meta = result.get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None:
            return f"No data for {symbol}."
        name = meta.get("shortName") or meta.get("longName") or symbol
        parts = [f"{name} ({sym}): {price:.4g} {meta.get('currency', '')}".rstrip()]
        if meta.get("regularMarketChangePercent") is not None:
            parts.append(f"day change {meta['regularMarketChangePercent']:+.2f}%")
        if meta.get("regularMarketDayHigh") is not None:
            parts.append(f"day range {meta['regularMarketDayLow']:.4g} - "
                         f"{meta['regularMarketDayHigh']:.4g}")
        if meta.get("regularMarketVolume"):
            parts.append(f"volume {int(meta['regularMarketVolume']):,}")
        return ". ".join(parts)
    except Exception as exc:
        return f"Quote lookup failed for {symbol}: {str(exc)}"


def convert_currency(amount: float, from_ccy: str, to_ccy: str) -> str:
    """Converts an amount from one currency to another, e.g.
    convert_currency(5, 'usd', 'eur')."""
    try:
        base = from_ccy.upper()
        data = json.loads(_http_get(f"https://open.er-api.com/v6/latest/{base}"))
        rate = data.get("rates", {}).get(to_ccy.upper())
        if not rate:
            return f"Unknown currency: {to_ccy}."
        value = float(amount) * rate
        return (f"{amount} {base} = {value:,.2f} {to_ccy.upper()} "
                f"(rate {rate}). Updated {data.get('time_last_update_utc', '?')}.")
    except Exception as exc:
        return f"Currency conversion failed: {str(exc)}"


def translate(text: str, to_lang: str, from_lang: str = "en") -> str:
    """Translates text into another language. to_lang is a two-letter code,
    e.g. 'fr', 'es', 'pt', 'de'."""
    try:
        pair = f"{from_lang}|{to_lang}".replace("|", "|")
        url = ("https://api.mymemory.translated.net/get?q="
               + urllib.parse.quote(text) + "&langpair=" + urllib.parse.quote(pair))
        data = json.loads(_http_get(url))
        out = data.get("responseData", {}).get("translatedText") or "no translation"
        return out
    except Exception as exc:
        return f"Translation failed: {str(exc)}"


def define_word(word: str) -> str:
    """Looks up a word in the dictionary and returns its meanings."""
    # Primary: Wiktionary REST API (fast, no key, rarely down)
    wik = ("https://en.wiktionary.org/api/rest_v1/page/definition/"
           + urllib.parse.quote(word))
    try:
        data = json.loads(_http_get(wik))
        lines = [word]
        for entry in data.get("en", []):
            pos = entry.get("partOfSpeech", "")
            for definition in entry.get("definitions", []):
                text = _clean(definition.get("definition", ""))
                if text:
                    lines.append(f"{pos}: {text[:400]}")
        if len(lines) > 1:
            return "\n".join(lines[:8])
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return f"'{word}' was not found in the dictionary."
    except Exception:
        pass
    # Fallback: free dictionary API
    url = "https://api.dictionaryapi.dev/api/v2/entries/en/" + urllib.parse.quote(word)
    try:
        entries = json.loads(_http_get(url, timeout=8))
        entry = entries[0]
        phonetic = entry.get("phonetic")
        if not phonetic:
            phonetic = next((p.get("text", "") for p in entry.get("phonetics", [])
                             if p.get("text")), "")
        lines = [f"{word}  {phonetic}".strip()]
        for meaning in entry.get("meanings", [])[:3]:
            for definition in meaning.get("definitions", [])[:2]:
                line = f"{meaning['partOfSpeech']}: {definition.get('definition')}"
                if definition.get("example"):
                    line += f"  (e.g. {definition['example']})"
                lines.append(line)
        return "\n".join(lines[:7])
    except urllib.error.HTTPError as exc:
        return f"Dictionary service error: {str(exc)}"
    except Exception as exc:
        return f"Could not define '{word}': {str(exc)}"


def current_time() -> str:
    """Returns the current date and time, local and UTC."""
    now = datetime.now().astimezone()
    utc = datetime.now(timezone.utc)
    return (f"Local: {now:%A, %Y-%m-%d %H:%M} ({now.tzname()})\n"
            f"UTC:   {utc:%Y-%m-%d %H:%M}Z")


_SAFE_NAMES = {n: getattr(math, n) for n in dir(math) if not n.startswith("_")}
_SAFE_NAMES.update({"abs": abs, "round": round, "min": min, "max": max,
                    "float": float, "int": int, "e": math.e, "pi": math.pi})


def calculate(expression: str) -> str:
    """Evaluates a math expression such as '9 + 10', 'sqrt(144) * 3',
    or '2^5'. Numbers and basic math names only - no code execution."""
    expr = (expression.replace("^", "**").replace("×", "*").replace("÷", "/"))
    if not re.fullmatch(r"[0-9a-zA-Z_\s+\-*/%().,\s]+", expr):
        return "Invalid expression. Numbers and math names only."
    try:
        return str(eval(expr, {"__builtins__": {}}, dict(_SAFE_NAMES)))  # noqa: S307
    except Exception as exc:
        return f"Could not calculate '{expression}': {str(exc)}"


def get_word_length(word: str) -> int:
    """Returns the number of characters in a word or phrase."""
    return len(word.strip())


# --- coding helpers (read-only; no arbitrary code execution) ---------------
import ast as _ast

# --- filesystem permission gate --------------------------------------------
# By default eLmi may ONLY read inside the project folder. Anything outside
# (Desktop, C:\, etc.) needs explicit user approval, given either interactively
# or via --allow-dir PATH (repeatable). This is a consent boundary, not a
# sandbox: it prevents silent access, not a determined local user.
_PROJECT_ROOT = os.path.dirname(os.path.abspath(_MEMORY_FILE))


def _is_allowed(path: str) -> bool:
    """True if path is inside the project folder or an approved directory."""
    try:
        root = os.path.abspath(path)
        proj = os.path.abspath(_PROJECT_ROOT)
        if os.path.commonpath([root, proj]) == proj:
            return True
        for allowed in _APPROVED_DIRS:
            if os.path.commonpath([root, os.path.abspath(allowed)]) == os.path.abspath(allowed):
                return True
    except ValueError:
        return False
    return False


def _denied_msg(path: str) -> str:
    return (f"Access denied: '{path}' is outside the project folder and not "
            f"approved. Run the agent with --allow-dir \"{path}\" (or grant "
            f"permission interactively) to let eLmi read there.")


INTERACTIVE = False


def _maybe_prompt_approval(path: str) -> bool:
    """If running interactively, ask the user to approve this path. Returns
    True if approved (and remember it for the rest of the session)."""
    if not INTERACTIVE or not sys.stdin.isatty():
        return False
    try:
        ans = input(f"\n[permission] Let eLmi access '{os.path.abspath(path)}'? [y/N] ").strip().lower()
    except Exception:
        return False
    if ans in ("y", "yes"):
        if os.path.isdir(path):
            add_approved_dir(path)
        return True
    return False


def _guard_path(path: str) -> str | None:
    """Permission gate for filesystem tools. Returns an error string if the
    path is NOT allowed, else None. Tries interactive approval first."""
    if _is_allowed(path):
        return None
    if _maybe_prompt_approval(path):
        return None
    return _denied_msg(path)


_APPROVED_DIRS = []
_APPROVED_EXEC = False   # becomes True after --allow-exec or interactive consent


def _exec_allowed() -> bool:
    """Code/shell execution requires explicit separately-confirmed consent."""
    return _APPROVED_EXEC


def _exec_denied_msg() -> str:
    return ("Execution disabled: eLmi won't run code or shell commands until "
            "you allow it. Start the agent with --allow-exec (or answer yes "
            "when it asks for permission).")


def _maybe_prompt_exec() -> bool:
    if _APPROVED_EXEC or not INTERACTIVE or not sys.stdin.isatty():
        return _APPROVED_EXEC
    try:
        ans = input("\n[permission] Enable eLmi to RUN code and shell "
                    "commands? [y/N] ").strip().lower()
    except Exception:
        return False
    if ans in ("y", "yes"):
        globals()["_APPROVED_EXEC"] = True
        return True
    return False


def _une(func, fallback):
    """Run func only if exec is approved; else return a clean denial."""
    if not _exec_allowed():
        if not _maybe_prompt_exec():
            return fallback
    return func()


def add_approved_dir(path: str) -> None:
    if path and os.path.isdir(path):
        _APPROVED_DIRS.append(os.path.abspath(path))


def _resolve_root(path: str) -> str:
    """Return an absolute root path, defaulting to the agent folder."""
    if not path or not path.strip():
        p = os.path.dirname(os.path.abspath(_MEMORY_FILE))
    else:
        p = os.path.abspath(os.path.expanduser(path.strip()))
    return p



def list_files(path: str = "") -> str:
    """Lists files and folders in a directory (does not enter hidden/system
    directories). Use this to explore a codebase."""
    try:
        root = _resolve_root(path)
        denied = _guard_path(root)
        if denied:
            return denied
        if not os.path.isdir(root):
            return f"Not a directory: {root}"
        entries = sorted(os.listdir(root))
        lines = []
        for name in entries:
            if name.startswith(".") or name in ("__pycache__", "node_modules",
                                                "venv", ".git", "site-packages"):
                continue
            full = os.path.join(root, name)
            tag = "DIR " if os.path.isdir(full) else "FILE"
            lines.append(f"{tag}  {name}")
        return f"{root}\n" + "\n".join(lines[:80])
    except Exception as exc:
        return f"Could not list directory: {str(exc)}"


def read_file(path: str, lines: int = 60) -> str:
    """Reads the first N lines of a text file. Returns an error for
    non-text/binary files or if the file is too large."""
    try:
        root = _resolve_root(path)
        denied = _guard_path(root)
        if denied:
            return denied
        if not os.path.isfile(root):
            return f"Not a file: {root}"
        if os.path.getsize(root) > 300_000:
            return f"File too large to read ({os.path.getsize(root)} bytes)."
        with open(root, "r", encoding="utf-8", errors="replace") as f:
            head = f.read().splitlines()[: lines]
        return f"{root}\n" + "\n".join(f"{i + 1}: {ln}" for i, ln in enumerate(head))
    except Exception as exc:
        return f"Could not read file: {str(exc)}"


def search_code(term: str, path: str = "", max_hits: int = 20) -> str:
    """Case-insensitive search for a term (text or regex) across text files
    in a folder. Returns file:line matches. Read-only."""
    try:
        root = _resolve_root(path)
        denied = _guard_path(root)
        if denied:
            return denied
        if not os.path.isdir(root):
            return f"Not a directory: {root}"
        pattern = re.compile(term, re.IGNORECASE)
        matches = []
        _IGN = {".git", "__pycache__", "node_modules", "venv", ".venv",
                "dist", "build", ".idea", "site-packages"}
        _EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json",
                ".md", ".txt", ".c", ".cpp", ".h", ".java", ".go", ".rs",
                ".sh", ".ps1", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _IGN]
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in _EXT:
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    with open(full, "r", encoding="utf-8", errors="replace") as f:
                        for n, ln in enumerate(f, 1):
                            if pattern.search(ln):
                                rel = os.path.relpath(full, root)
                                matches.append(f"{rel}:{n}: {ln.strip()[:150]}")
                                if len(matches) >= max_hits:
                                    return (f"{root} ({len(matches)} shown)"
                                            + "\n" + "\n".join(matches))
                except Exception:
                    continue
        if not matches:
            return f"No matches for '{term}' in {root}."
        return f"{root} ({len(matches)} matches)\n" + "\n".join(matches[:max_hits])
    except Exception as exc:
        return f"Search failed: {str(exc)}"


def check_syntax(code: str, language: str = "python") -> str:
    """Syntax-checks a snippet of code WITHOUT running it. Supports
    python, json, and javascript (via a safe js2py-free heuristic)."""
    lang = (language or "python").lower()
    if lang in ("python", "py"):
        try:
            _ast.parse(code)
            return "Python: syntax OK."
        except SyntaxError as exc:
            return f"Python: syntax error - {exc}"
    if lang in ("json",):
        try:
            json.loads(code)
            return "JSON: valid."
        except ValueError as exc:
            return f"JSON: invalid - {exc}"
    if lang in ("jsonc", "json5"):
        try:
            json.loads(re.sub(r"/\*.*?\*/|//[^\n]*", "", code, flags=re.S))
            return "JSON-ish: valid (comments stripped)."
        except ValueError as exc:
            return f"JSON-ish: invalid - {exc}"
    return (f"check_syntax supports python, json, jsonc only right now "
            f"(got '{language}').")


_CWD = _PROJECT_ROOT   # eLmi's real shell working directory; persistent across calls


def _set_cwd_to(path: str) -> str:
    global _CWD
    try:
        p = os.path.abspath(path)
        if not os.path.isdir(p):
            return f"No such directory: {p}"
        _CWD = p
        return f"Working directory changed to {p}"
    except Exception as exc:
        return f"Could not change directory: {str(exc)}"


def get_pwd() -> str:
    """Returns the current terminal working directory eLmi is operating in."""
    return _CWD


def change_directory(path: str) -> str:
    """Changes eLmi's working directory (requires exec permission)."""
    return _une(lambda: _set_cwd_to(path), _exec_denied_msg())


def run_python(code: str, timeout: int = 10) -> str:
    """Runs a Python snippet in a REAL Python interpreter (normal I/O,
    normal stdlib, no sandbox) and returns its printed output. Runs in
    eLmi's current working directory. Requires exec permission. This is
    full-strength Python - use with the same care you would any shell."""
    def _run():
        if len(code) > 6000:
            return "Code too long (max 6000 chars)."
        import subprocess as _sp
        try:
            result = _sp.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, timeout=timeout,
                cwd=_CWD,
            )
        except _sp.TimeoutExpired:
            return f"Timed out after {timeout}s."
        except Exception as exc:
            return f"Could not start runner: {str(exc)}"
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        parts = [f"(exit code {result.returncode}, cwd: {_CWD})"]
        if out:
            parts.append("stdout:\n" + out[:6000])
        if err:
            parts.append("stderr:\n" + err[:4000])
        if not parts[1:]:
            return "(no output) " + parts[0]
        return "\n\n".join(parts)
    return _une(_run, _exec_denied_msg())


def run_command(command: str, timeout: int = 30) -> str:
    """Runs a real PowerShell command and returns its output. Runs in
    eLmi's current working directory. Use 'cd path' to move somewhere.
    Requires exec permission. This has full machine reach (pip, git,
    files, system) - exactly like a human terminal."""
    def _run():
        if len(command) > 4000:
            return "Command too long (max 4000 chars)."
        import subprocess as _sp
        cmd = f"cd '{_CWD}'; {command} "
        try:
            result = _sp.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True, text=True, timeout=timeout,
                cwd=_PROJECT_ROOT,
            )
        except _sp.TimeoutExpired:
            return f"Command timed out after {timeout}s."
        except Exception as exc:
            return f"Could not run command: {str(exc)}"
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        parts = [f"(exit code {result.returncode}, cwd: {_CWD})"]
        if out:
            parts.append("stdout:\n" + out[:6000])
        if err:
            parts.append("stderr:\n" + err[:4000])
        return "\n\n".join(parts)
    return _une(_run, _exec_denied_msg())


# --- persistent notes (memory) ---------------------------------------------
def _load_mem() -> list:
    if not os.path.exists(_MEMORY_FILE):
        return []
    try:
        with open(_MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_mem(store: list) -> None:
    with open(_MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(store[-100:], f, ensure_ascii=False, indent=2)


def remember(note: str) -> str:
    """Saves a note that is kept between sessions. Use this for user
    preferences, facts, or anything to remember later."""
    store = _load_mem()
    store.append({"ts": datetime.now().astimezone().isoformat(timespec="minutes"),
                  "note": note[:2000]})
    _save_mem(store)
    return "Noted."


def recall(topic: str) -> str:
    """Reads back previously saved notes that mention the topic."""
    store = _load_mem()
    hits = [n for n in store if topic.lower() in n["note"].lower()]
    if not hits:
        return f"No saved notes found about '{topic}'."
    return "\n".join(f"[{n['ts']}] {n['note']}" for n in hits[-8:])


# ---------------------------------------------------------------------------
# Tool registry + schemas (built from one spec table)
# ---------------------------------------------------------------------------
TOOL_SPECS = {
    "search_web": {
        "desc": "Searches the internet for current information about any topic.",
        "params": {"query": ("string", "The search query", True)},
    },
    "get_word_length": {
        "desc": "Returns the number of characters in a word or phrase.",
        "params": {"word": ("string", "The word or phrase", True)},
    },
    "calculate": {
        "desc": "Evaluates a math expression such as '9 + 10' or 'sqrt(144)'.",
        "params": {"expression": ("string", "The math expression", True)},
    },
    "open_url": {
        "desc": "Fetches a web page and returns its title and readable text.",
        "params": {"url": ("string", "The web address (http/https) to fetch", True)},
    },
    "get_weather": {
        "desc": "Returns the current weather for the given city.",
        "params": {"city": ("string", "City name, e.g. 'Accra'", True)},
    },
    "get_quote": {
        "desc": "Returns the latest price for a stock, crypto, index, or currency pair "
                "e.g. AAPL, BTC-USD, ^GSPC, EURUSD=X.",
        "params": {"symbol": ("string", "Symbol: stock like 'AAPL', crypto like 'BTC-USD', "
                                       "index like '^GSPC', or forex like 'EURUSD=X'", True)},
    },
    "convert_currency": {
        "desc": "Converts an amount from one currency to another.",
        "params": {
            "amount": ("number", "Amount to convert", True),
            "from_ccy": ("string", "Source currency code, e.g. 'usd'", True),
            "to_ccy": ("string", "Target currency code, e.g. 'eur'", True),
        },
    },
    "translate": {
        "desc": "Translates text into another language.",
        "params": {
            "text": ("string", "Text to translate", True),
            "to_lang": ("string", "Two-letter target language code, e.g. 'fr'", True),
        },
    },
    "define_word": {
        "desc": "Looks up a word in the dictionary and returns its meanings.",
        "params": {"word": ("string", "The word to look up", True)},
    },
    "current_time": {
        "desc": "Returns the current date and time, local and UTC.",
        "params": {},
    },
    "remember": {
        "desc": "Saves a note that is kept between sessions.",
        "params": {"note": ("string", "The note to save", True)},
    },
    "recall": {
        "desc": "Reads back previously saved notes that mention the topic.",
        "params": {"topic": ("string", "A word or phrase to search the notes for", True)},
    },
    "list_files": {
        "desc": "Lists the files and folders in a directory, to explore a codebase. "
                "Read-only. Optional path defaults to the project folder.",
        "params": {"path": ("string", "Directory path (optional)", False)},
    },
    "read_file": {
        "desc": "Reads the first lines of a text file. Read-only.",
        "params": {"path": ("string", "File path to read", True)},
    },
    "search_code": {
        "desc": "Searches the codebase for a word or regex across text files. "
                "Returns file:line matches. Read-only.",
        "params": {
            "term": ("string", "Word or regex to search for", True),
            "path": ("string", "Directory to search (optional)", False),
        },
    },
    "check_syntax": {
        "desc": "Syntax-checks a code snippet WITHOUT running it. Formats: "
                "python, json, jsonc.",
        "params": {
            "code": ("string", "The code snippet to check", True),
            "language": ("string", "Language: python, json, or jsonc", False),
        },
    },
    "run_python": {
        "desc": "Runs Python in a REAL interpreter (full stdlib, full strength, "
                "no sandbox) in eLmi's current working directory and returns "
                "its output. Requires exec permission.",
        "params": {
            "code": ("string", "The Python code to run (uses print() for output)", True),
            "timeout": ("number", "Seconds to allow (default 10)", False),
        },
    },
    "run_command": {
        "desc": "Runs a REAL PowerShell command with full machine reach (pip, "
                "git, files, system) and returns output, in eLmi's working "
                "directory. Use 'cd <path>' to move directories first. "
                "Requires exec permission.",
        "params": {
            "command": ("string", "The PowerShell command to run", True),
            "timeout": ("number", "Seconds to allow (default 30)", False),
        },
    },
    "get_pwd": {
        "desc": "Returns eLmi's current terminal working directory.",
        "params": {},
    },
    "change_directory": {
        "desc": "Changes eLmi's working directory, affecting later run_command "
                "and run_python calls. Requires exec permission.",
        "params": {"path": ("string", "Directory to move to (absolute or relative)", True)},
    },
}

TOOLS = {name: globals()[name] for name in TOOL_SPECS}


def _build_schemas():
    schemas = []
    for name, spec in TOOL_SPECS.items():
        props = {}
        required = []
        for pname, (ptype, pdesc, needed) in spec["params"].items():
            props[pname] = {"type": ptype, "description": pdesc}
            if needed:
                required.append(pname)
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": spec["desc"],
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": required,
                },
            },
        })
    return schemas


TOOL_SCHEMAS = _build_schemas()

SYSTEM_PROMPT = """You are eLmi, a powerful research AND coding agent with 20 tools.

Research tools:
- search_web(query): current info on any topic
- open_url(url): fetch a web page and read its text
- get_weather(city): current weather
- get_quote(symbol): price for stocks, crypto, indices, or forex
- convert_currency(amount, from_ccy, to_ccy): currency conversion
- translate(text, to_lang): machine translation
- define_word(word): dictionary definitions
- calculate(expression): math (supports sqrt, pi, powers)
- get_word_length(word): counts characters
- current_time(): date and time

Memory tools:
- remember(text): save a note between sessions
- recall(topic): read back saved notes

Coding tools (read-only):
- list_files(path): list a directory to explore a project
- read_file(path): read a text file
- search_code(term, path): regex search across source files
- check_syntax(code, language): syntax-check code without running it
- get_pwd(): current directory eLmi is working in

Execution tools (REAL terminal control - full machine reach like a human
shell; requires the user to have enabled execution):
- change_directory(path): move eLmi's working directory
- run_command(command, timeout): run real PowerShell (pip, git, files, sys)
- run_python(code, timeout): run real Python, full stdlib

Rules:
1. Use a tool when the question needs current info, math, weather, prices,
   currency, definitions, translations, time, or a look at code - never guess.
2. If a question has multiple parts, call tools for each part before answering.
3. If a tool fails, tell the user and try a different approach.
4. Within this conversation, the messages you can see are your real history -
   refer to them freely. Across separate sessions you forget everything except
   your remember() notes. So when the user asks 'do you remember', 'what did I
   tell you', or anything about your own memory or past chats, ALWAYS call
   recall() to check your notes before answering. Save user preferences and
   key facts with remember().
5. Keep answers clear, accurate, and in plain English."""


# ---------------------------------------------------------------------------
# Web search via Bing HTML (pure stdlib; no API key, no compiled deps)
# ---------------------------------------------------------------------------
def _bing(query: str) -> str:
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    html = _http_get(url)

    pairs = re.findall(r'<h2[^>]*><a[^>]*>(.*?)</a></h2>.*?<p[^>]*>(.*?)</p>', html, re.S)

    lines = []
    for title, snip in pairs[:5]:
        t = _clean(title)
        s = _clean(snip)
        if t:
            lines.append(f"{t}: {s}" if s else t)
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# OpenRouter API
# ---------------------------------------------------------------------------
def _call_model(messages, retries=3):
    payload = {
        "model": MODEL,
        "messages": messages,
        "tools": TOOL_SCHEMAS,
        "temperature": 0,
        "max_tokens": 2048,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY', '')}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "ignore")
            last = f"API error {exc.code}: {body[:300]}"
            if exc.code in (401, 403, 404):  # permanent - do not retry
                raise
            time.sleep(2 * (attempt + 1))
        except Exception as exc:
            last = f"Request failed: {str(exc)}"
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(last)


def _extract_json(s: str) -> dict:
    """Resists stray markdown fences / trailing commas around tool args."""
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        cleaned = re.sub(r",\s*([}\]])", r"\1", m.group(0))
        try:
            return json.loads(cleaned)
        except ValueError:
            pass
    return {}


def _clip(s: str, n: int = RESULT_LIMIT) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n] + "\n...[truncated]"


def _args_str(args: dict) -> str:
    parts = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:3])
    return parts[:100]


def ask(question: str, history=None, verbose: bool = True, on_tool=None,
        attachments=None) -> str:
    """Runs the agent loop for one question, returns the final answer.

    history: list of (user_text, agent_text) from earlier turns in this
    session, used so eLmi remembers the conversation.
    verbose: prints each tool call as it runs.
    on_tool: optional callback on_tool(name, args, result) fired after each
             tool executes (used by the GUI to stream traces live).
    attachments: optional list of (display_name, abs_path, kind) held by the
                 GUI (kind in {"text", "image", "other"}). Their paths are
                 appended to the user message so eLmi can read/open them.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for user_q, answer in (history or [])[-6:]:
        messages.append({"role": "user", "content": user_q})
        messages.append({"role": "assistant", "content": answer})
    if attachments:
        lines = ["ATTACHED FILES (use read_file / run_command to access them):"]
        for name, path, kind in attachments:
            lines.append(f"- [{kind}] {name}  ->  {path}")
        question = question + "\n\n" + "\n".join(lines)
    messages.append({"role": "user", "content": question})

    for _ in range(MAX_STEPS):
        try:
            reply = _call_model(messages)
        except urllib.error.HTTPError as exc:
            return f"API error {exc.code}: {exc.read().decode('utf-8', 'ignore')[:300]}"
        except RuntimeError as exc:
            return f"Request failed after retries: {str(exc)[:300]}"
        except Exception as exc:
            return f"Request failed: {str(exc)}"

        choice = reply.get("choices", [{}])[0]
        msg = choice.get("message") or {}

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            content = msg.get("content") or "(no content)"
            if content == "(no content)" and reply.get("error"):
                content = str(reply["error"])
            return content

        messages.append(msg)
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name, args = fn.get("name", "?"), _extract_json(fn.get("arguments") or "{}")
            if verbose and name != "?":
                print(f"  -> eLmi called {name}({_args_str(args)})")
            tool = TOOLS.get(name)
            try:
                result = tool(**args) if tool else f"Unknown tool: {name}"
            except TypeError as exc:
                result = f"{name} rejected arguments: {str(exc)}"
            except Exception as exc:
                result = f"{name} failed: {str(exc)}"
            if on_tool and name != "?":
                on_tool(name, args, str(result))
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "content": _clip(str(result)),
            })
        time.sleep(0.4)  # be gentle on free-tier rate limits

    return "Stopped after maximum steps."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    single_shot, question, model = False, None, None
    argv = list(sys.argv[1:])
    while argv:
        arg = argv.pop(0)
        if arg == "--once":
            single_shot = True
        elif arg == "--model" and argv:
            model = argv.pop(0)
        elif arg == "--allow-dir" and argv:
            add_approved_dir(argv.pop(0))
        elif arg == "--allow-exec":
            globals()["_APPROVED_EXEC"] = True
        elif arg.startswith("-") and arg not in ("-h", "--help"):
            print(f"Unknown flag: {arg}")
            return
        elif arg in ("-h", "--help"):
            print("Usage: python agent.py [--once] [\"question\"] [--model SLUG] "
                  "[--allow-dir PATH ...] [--allow-exec]")
            print("  --once            single-shot mode (optional question follows)")
            print("  --allow-dir PATH  let eLmi read a directory outside the "
                  "project folder (repeatable)")
            print("  --allow-exec      let eLmi run Python and shell commands "
                  "(REAL terminal, full machine reach)")
            return
        else:
            if question is None:
                question = arg
            else:
                question += " " + arg

    global MODEL, INTERACTIVE
    if model:
        MODEL = model
    if not single_shot and sys.stdin.isatty():
        INTERACTIVE = True

    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key or key.startswith("sk-or-v1-your-key"):
        print("ERROR: Set your real OPENROUTER_API_KEY in agent/.env")
        sys.exit(1)

    if single_shot and question:
        print(ask(question, verbose=True))
        return

    print(f"eLmi 3005 ready. {len(TOOLS)} tools online. "
          f"Type your question or 'quit' to exit.\n")
    history = []
    while True:
        q = input("You: ").strip()
        if q.lower() in ("quit", "exit", "q"):
            break
        if not q:
            continue
        answer = ask(q, history=history, verbose=True)
        print("Agent:", answer, "\n")
        history.append((q, answer))
        history = history[-12:]


if __name__ == "__main__":
    main()