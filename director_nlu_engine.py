# -*- coding: utf-8 -*-
"""
Global Spirit Group ★ Director Conversational NLU & Execution Engine (v34.3)

Upgrades Director Tim (and every member's Personal AI Director in @unitybusinessbot
and personal Telegram bots) from static canned confirmations to genuine bilingual
(Russian & English) comprehension and execution:
  1. Multi-turn per-chat conversation memory (remembers recent URLs, topics, active
     folder, and last GitHub Issue so "Шаг #1" / follow-ups bind to real context).
  2. Accurate bilingual RU/EN intent & entity routing:
       - Task audits & live status ("проверь новые задачи", "статус #67") without
         creating junk duplicate issues.
       - Follow-up execution ("выполни шаг #1", "делай дальше", "use notebooklm on this")
         bound to previous turn URL/issue context.
       - Deep URL / NotebookLM / Web & Office-RAG research executed non-blocking in a
         background daemon thread via GitHub Models (openai/gpt-4.1 -> gpt-4o ->
         gpt-4.1-mini), SSRF-safe URL fetching, office_memory.json RAG, and local
         `nlm` CLI when authenticated.
       - Direct natural-language Director conversation & brainstorming.
  3. Automatic delivery of real answers back to Telegram (with Markdown->HTML safe
     rendering and chunking) and structured persistence to GitHub Issues & Data branch.
"""

import base64
import datetime
import html as _html
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTEXT_FILE = os.path.join(BASE_DIR, ".director_chat_context.json")
_CTX_LOCK = threading.Lock()
_ACTIVE_EXEC_LOCK = threading.Lock()
_ACTIVE_EXEC_KEYS = {}  # key -> started_ts

GH_MODELS_URL = "https://models.github.ai/inference/chat/completions"
DEEP_MODELS_CHAIN = ("openai/gpt-4.1", "openai/gpt-4o", "openai/gpt-4.1-mini", "openai/gpt-4o-mini")
FAST_MODELS_CHAIN = ("openai/gpt-4.1-mini", "openai/gpt-4o-mini")

PROJECT_ALIASES = [
    ("Alberta", ("alberta", "альберт", "аля", "aliashirani", "alberta-hub")),
    ("True 1 Team", ("true 1", "true1", "тру 1", "тру1", "true 1 team")),
    ("Юра", ("юра", "юр", "yvyatkin", "кхл", "khl", "крипт")),
    ("Татьяна tentorium.ru", ("татьяна", "тат", "tentorium", "тенториум")),
    ("Traffic", ("traffic", "трафик", "реклам")),
    ("Входящие_Офис", ("входящие", "входящ", "inbox")),
    ("Главный Штаб", ("главный штаб", "главном штабе", "штаб", "main hq", "hq")),
]


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _esc(val):
    return _html.escape(str(val or ""), quote=True)


def extract_urls(text):
    raw = re.findall(r"https?://[^\s<>\"'\)\]}«»]+", str(text or ""))
    seen = set()
    out = []
    for u in raw:
        cleaned = u.rstrip(".,;:!?»«\"')]} ")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def detect_lang(text):
    s = str(text or "")
    cyr = len(re.findall(r"[а-яА-ЯёЁ]", s))
    lat = len(re.findall(r"[a-zA-Z]", s))
    if cyr >= 3:
        return "ru"
    if lat >= 4 and cyr == 0:
        return "en"
    return "ru" if cyr >= lat else "en"


def strip_html_tags(text):
    s = re.sub(r"<br\s*/?>", "\n", str(text or ""), flags=re.IGNORECASE)
    s = re.sub(r"</?(?:b|i|code|pre|a|u|s|blockquote)(?:\s+[^>]*)?>", "", s, flags=re.IGNORECASE)
    return _html.unescape(s)


def markdown_to_tg_html(text):
    """Convert markdown-ish LLM text into strictly valid Telegram HTML subset."""
    raw = str(text or "").strip()
    if not raw:
        return ""
    placeholders = {}

    def _hold(snippet):
        key = f"\x00PH{len(placeholders)}\x00"
        placeholders[key] = snippet
        return key

    # Protect fenced code blocks first
    def _repl_fenced(m):
        code = _esc(m.group(1).strip("\r\n"))
        return _hold(f"<pre>{code}</pre>")

    work = re.sub(r"```(?:[a-zA-Z0-9_+-]+)?\s*\n?(.*?)```", _repl_fenced, raw, flags=re.DOTALL)

    # Protect inline code spans
    def _repl_inline(m):
        return _hold(f"<code>{_esc(m.group(1))}</code>")

    work = re.sub(r"`([^`\n]+)`", _repl_inline, work)

    # Protect markdown links [label](https://...)
    def _repl_link(m):
        lbl = _esc(m.group(1))
        url = _esc(m.group(2))
        if url.startswith(("http://", "https://")):
            return _hold(f'<a href="{url}">{lbl}</a>')
        return lbl

    work = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", _repl_link, work)

    # Escape remaining text before injecting bold/italic tags
    work = _esc(work)

    # Convert markdown headings (# Heading -> bold line)
    work = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", work, flags=re.MULTILINE)
    # Convert bullet list prefixes cleanly
    work = re.sub(r"^[\-*]\s+", "• ", work, flags=re.MULTILINE)
    # Bold **...** and __...__
    work = re.sub(r"\*\*([^\*\n]+?)\*\*", r"<b>\1</b>", work)
    work = re.sub(r"__([^_\n]+?)__", r"<b>\1</b>", work)

    for key, val in placeholders.items():
        work = work.replace(key, val)
    return work


def split_tg_chunks(text, limit=3800):
    s = str(text or "").strip()
    if len(s) <= limit:
        return [s] if s else []
    chunks = []
    remaining = s
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < int(limit * 0.45):
            cut = remaining.rfind("\n", 0, limit)
        if cut < int(limit * 0.45):
            cut = remaining.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def send_tg_smart(ctx, bot_token, chat_id, text_body, reply_markup=None, bot_label="", as_markdown=False):
    html_text = markdown_to_tg_html(text_body) if as_markdown else str(text_body or "")
    chunks = split_tg_chunks(html_text, limit=3800)
    if not chunks:
        return None
    call_tg = ctx["call_tg"]
    last_res = None
    for idx, part in enumerate(chunks):
        payload = {
            "chat_id": str(chat_id),
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup and idx == len(chunks) - 1:
            payload["reply_markup"] = reply_markup
        res = call_tg("sendMessage", payload, bot_token=bot_token, bot_label=bot_label)
        if not res or not res.get("ok"):
            # Safe plain-text fallback so Telegram HTML quirks never drop an answer
            plain_payload = {
                "chat_id": str(chat_id),
                "text": strip_html_tags(part)[:4000],
                "disable_web_page_preview": True,
            }
            if reply_markup and idx == len(chunks) - 1:
                plain_payload["reply_markup"] = reply_markup
            res = call_tg("sendMessage", plain_payload, bot_token=bot_token, bot_label=bot_label)
        last_res = res
    return last_res


# ---------------------------------------------------------------------------
# Multi-turn Persistent Conversation Memory
# ---------------------------------------------------------------------------

def _load_all_contexts():
    if not os.path.exists(CONTEXT_FILE):
        return {}
    try:
        with open(CONTEXT_FILE, "r", encoding="utf-8") as f:
            obj = json.load(f)
            return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _save_all_contexts(data):
    try:
        with open(CONTEXT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _ctx_key(bot_key, chat_id):
    return f"{str(bot_key or 'main_hq').strip().lower()}:{str(chat_id or '').strip()}"


def get_chat_context(bot_key, chat_id, default_project="Главный Штаб"):
    key = _ctx_key(bot_key, chat_id)
    with _CTX_LOCK:
        all_ctx = _load_all_contexts()
        cur = all_ctx.get(key)
        if not isinstance(cur, dict):
            cur = {
                "active_project": default_project or "Главный Штаб",
                "last_issue_number": None,
                "last_issue_title": "",
                "last_urls": [],
                "last_topic": "",
                "last_answer_preview": "",
                "turns": [],
                "updated_at": _utc_now(),
            }
        else:
            cur.setdefault("active_project", default_project or "Главный Штаб")
            cur.setdefault("turns", [])
            cur.setdefault("last_urls", [])
        return dict(cur)


def update_chat_context(bot_key, chat_id, turn_entry=None, **fields):
    key = _ctx_key(bot_key, chat_id)
    with _CTX_LOCK:
        all_ctx = _load_all_contexts()
        cur = all_ctx.get(key)
        if not isinstance(cur, dict):
            cur = {"active_project": "Главный Штаб", "turns": [], "last_urls": []}
        for k, v in fields.items():
            if v is not None:
                cur[k] = v
        if isinstance(turn_entry, dict):
            turns = cur.setdefault("turns", [])
            turns.append(turn_entry)
            cur["turns"] = turns[-12:]
        cur["updated_at"] = _utc_now()
        all_ctx[key] = cur
        if len(all_ctx) > 120:
            # Prune oldest contexts safely
            ordered = sorted(all_ctx.items(), key=lambda kv: str((kv[1] or {}).get("updated_at") or ""))
            for old_k, _ in ordered[:-100]:
                all_ctx.pop(old_k, None)
        _save_all_contexts(all_ctx)
        return dict(cur)


# ---------------------------------------------------------------------------
# Project Isolation & Folder Resolution
# ---------------------------------------------------------------------------

def user_can_see_project(ctx, user_obj, project_name, is_owner=False):
    if is_owner:
        return True
    lg = str((user_obj or {}).get("login") or "").strip().lower().lstrip("@")
    if ctx["is_owner_login"](lg):
        return True
    if bool((user_obj or {}).get("all_projects")) or bool((user_obj or {}).get("has_full_access")):
        return True
    assigned = [str(p or "").strip().lower() for p in ((user_obj or {}).get("assigned_projects") or [])]
    if "*" in assigned:
        return True
    pn = str(project_name or "").strip().lower()
    if not pn:
        return False
    return any(pn == ap or pn in ap or ap in pn for ap in assigned if ap)


def resolve_project_mention(text, current_project, ctx, user_obj, is_owner=False):
    low = str(text or "").lower()
    for canonical, kws in PROJECT_ALIASES:
        if any(kw in low for kw in kws):
            if user_can_see_project(ctx, user_obj, canonical, is_owner=is_owner):
                return canonical, True
    if not is_owner:
        assigned = [p for p in ((user_obj or {}).get("assigned_projects") or []) if p != "*"]
        for ap in assigned:
            if ap.lower() in low:
                return ap, True
    return current_project, False


# ---------------------------------------------------------------------------
# SSRF-Safe URL Fetching & OG Social Preview Extraction
# ---------------------------------------------------------------------------

def _is_public_hostname(hostname):
    host = str(hostname or "").strip().rstrip(".")
    if not host or host.lower() in ("localhost",):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        ip_str = info[4][0].split("%")[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme not in ("http", "https") or not _is_public_hostname(parsed.hostname):
            raise urllib.error.URLError(f"Blocked unsafe redirect host: {parsed.hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_url_metadata_safe(url, timeout=9):
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not _is_public_hostname(parsed.hostname):
        return {"url": url, "ok": False, "error": "blocked_or_invalid_host"}

    host_low = (parsed.hostname or "").lower()
    is_social = any(d in host_low for d in ("facebook.com", "fb.watch", "instagram.com", "tiktok.com", "youtube.com", "youtu.be"))
    uas = [
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)" if is_social
        else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    ]
    opener = urllib.request.build_opener(_SafeRedirectHandler())

    last_err = ""
    for ua in uas:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"})
            with opener.open(req, timeout=timeout) as resp:
                final_url = resp.geturl()
                ctype = str(resp.headers.get("Content-Type") or "")
                raw = resp.read(140000).decode("utf-8", errors="replace")
            title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
            og_title = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', raw, re.IGNORECASE)
            og_desc = re.search(r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)', raw, re.IGNORECASE)
            meta_desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', raw, re.IGNORECASE)
            body_clean = re.sub(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
            body_clean = re.sub(r"<[^>]+>", " ", body_clean)
            body_clean = re.sub(r"\s+", " ", _html.unescape(body_clean)).strip()
            t_val = _html.unescape((og_title.group(1) if og_title else (title_m.group(1) if title_m else ""))).strip()
            d_val = _html.unescape((og_desc.group(1) if og_desc else (meta_desc.group(1) if meta_desc else ""))).strip()
            if "youtube.com" in host_low or "youtu.be" in host_low:
                yt_desc_m = re.search(r'"shortDescription":"((?:\\.|[^"\\])*)"', raw)
                if yt_desc_m:
                    try:
                        yt_desc = json.loads('"' + yt_desc_m.group(1) + '"')
                        if yt_desc and len(yt_desc) > len(d_val):
                            d_val = yt_desc[:1400]
                            body_clean = yt_desc[:2400] + (" " + body_clean if body_clean else "")
                    except Exception:
                        pass
            if not is_social or t_val or d_val:
                return {
                    "url": url,
                    "final_url": final_url,
                    "ok": True,
                    "content_type": ctype,
                    "title": t_val[:260],
                    "description": d_val[:700],
                    "text_excerpt": body_clean[:2400],
                }
        except Exception as e:
            last_err = str(e)

    # YouTube fallback via public oEmbed API (works even when watch HTML is JS-gated)
    if "youtube.com" in host_low or "youtu.be" in host_low:
        try:
            oe_url = "https://www.youtube.com/oembed?" + urllib.parse.urlencode({"url": str(url), "format": "json"})
            req = urllib.request.Request(oe_url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with opener.open(req, timeout=timeout) as resp:
                oe = json.loads(resp.read().decode("utf-8", errors="replace"))
            vt = str(oe.get("title") or "").strip()
            va = str(oe.get("author_name") or "").strip()
            vau = str(oe.get("author_url") or "").strip()
            if vt:
                desc_line = f"YouTube: {vt}" + (f" · Канал: {va}" if va else "") + (f" ({vau})" if vau else "")
                return {
                    "url": url,
                    "final_url": url,
                    "ok": True,
                    "content_type": "application/json+oembed",
                    "title": vt[:260],
                    "description": desc_line[:700],
                    "text_excerpt": desc_line[:2400],
                }
        except Exception as oe_err:
            last_err = last_err or str(oe_err)

    return {"url": url, "ok": False, "error": last_err or "fetch_failed"}


# ---------------------------------------------------------------------------
# NotebookLM CLI Check & Execution Helper
# ---------------------------------------------------------------------------

def check_nlm_status():
    exe = shutil.which("nlm")
    prof_dir = os.path.expanduser("~/.notebooklm-mcp-cli/profiles")
    profiles = []
    if os.path.isdir(prof_dir):
        profiles = [d for d in os.listdir(prof_dir) if os.path.isdir(os.path.join(prof_dir, d))]
    if not exe:
        return {"installed": False, "authenticated": False, "reason": "nlm CLI не установлен"}
    if not profiles:
        return {
            "installed": True,
            "authenticated": False,
            "exe": exe,
            "reason": "nlm.exe найден на ПК, но профиль Google не авторизован (нужно 1 раз выполнить `nlm login` в окне Chrome)",
        }
    return {"installed": True, "authenticated": True, "exe": exe, "profiles": profiles, "reason": "ok"}


def try_nlm_research(question, urls=None, topic=""):
    """Queries NotebookLM if locally authenticated; never fabricates results."""
    st = check_nlm_status()
    if not st.get("authenticated"):
        return {"used": False, "status": st}
    prof = (st.get("profiles") or ["default"])[0]
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    try:
        list_res = subprocess.run(
            [st["exe"], "notebook", "list", "--profile", prof, "--json"],
            capture_output=True, text=True, timeout=18, stdin=subprocess.DEVNULL, creationflags=no_window
        )
        if list_res.returncode != 0:
            return {"used": False, "status": {"installed": True, "authenticated": False, "reason": (list_res.stderr or list_res.stdout)[:180]}}
        nb_id = None
        try:
            parsed = json.loads(list_res.stdout or "[]")
            items = parsed if isinstance(parsed, list) else (parsed.get("notebooks") or [])
            for nb in items:
                if "global spirit" in str(nb.get("title") or "").lower() or "director" in str(nb.get("title") or "").lower():
                    nb_id = nb.get("id") or nb.get("notebook_id")
                    break
            if not nb_id and items:
                nb_id = items[0].get("id") or items[0].get("notebook_id")
        except Exception:
            pass
        if not nb_id:
            cr = subprocess.run(
                [st["exe"], "notebook", "create", f"GSG Director Research {topic[:30]}".strip(), "--profile", prof, "--json"],
                capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL, creationflags=no_window
            )
            try:
                nb_obj = json.loads(cr.stdout or "{}")
                nb_id = nb_obj.get("id") or nb_obj.get("notebook_id")
            except Exception:
                nb_id = None
        if not nb_id:
            return {"used": False, "status": {"installed": True, "authenticated": False, "reason": "Не удалось открыть блокнот NLM"}}
        for u in (urls or [])[:2]:
            subprocess.run(
                [st["exe"], "source", "add", str(nb_id), "--url", str(u), "--profile", prof],
                capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL, creationflags=no_window
            )
        q_res = subprocess.run(
            [st["exe"], "notebook", "query", str(nb_id), str(question)[:600], "--profile", prof],
            capture_output=True, text=True, timeout=45, stdin=subprocess.DEVNULL, creationflags=no_window
        )
        ans = (q_res.stdout or "").strip()
        if q_res.returncode == 0 and ans:
            return {"used": True, "notebook_id": nb_id, "answer": ans[:3000], "status": st}
        return {"used": False, "status": {"installed": True, "authenticated": True, "reason": (q_res.stderr or "empty_nlm_answer")[:180]}}
    except Exception as e:
        return {"used": False, "status": {"installed": True, "authenticated": False, "reason": str(e)[:180]}}


# ---------------------------------------------------------------------------
# Multi-Backend AI Router (Gemini -> Local Ollama -> Validated GitHub Models)
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
_OLLAMA_TAGS_CACHE = {"ts": 0.0, "models": []}


def _get_ollama_models(force=False):
    now = time.time()
    if not force and (now - _OLLAMA_TAGS_CACHE["ts"] < 30):
        return list(_OLLAMA_TAGS_CACHE["models"])
    found = []
    try:
        req = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2.5) as r:
            obj = json.loads(r.read().decode("utf-8"))
            for m in (obj.get("models") or []):
                n = str(m.get("name") or "").strip()
                if n:
                    found.append(n)
    except Exception:
        found = []
    _OLLAMA_TAGS_CACHE["ts"] = now
    _OLLAMA_TAGS_CACHE["models"] = found
    return list(found)


def _pick_ollama_model(installed):
    if not installed:
        return ""
    preferred = ("qwen2.5:3b", "qwen2.5:7b", "qwen2.5:1.5b", "qwen2.5", "llama3.2", "llama3.1", "gemma2", "mistral")
    for p in preferred:
        for inst in installed:
            if inst == p or inst.startswith(p + ":"):
                return inst
    return installed[0]


def _call_ollama_chat(model_name, messages, max_tokens=1400, temperature=0.25, timeout=45, json_mode=False):
    payload = {
        "model": model_name,
        "messages": messages,
        "stream": False,
        "keep_alive": "10m",
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    if json_mode:
        payload["format"] = "json"
    req = urllib.request.Request(
        f"{OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        parsed = json.loads(r.read().decode("utf-8"))
    txt = str(((parsed.get("message") or {}).get("content")) or "").strip()
    return txt


def _resolve_runtime_gemini_key(ctx_val=""):
    if ctx_val and str(ctx_val).strip():
        return str(ctx_val).strip()
    env_k = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if env_k:
        return env_k
    cfg_p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tg_daemon_config.json")
    if os.path.exists(cfg_p):
        try:
            with open(cfg_p, "r", encoding="utf-8-sig") as f:
                k = str(json.load(f).get("gemini_api_key") or "").strip()
                if k:
                    return k
        except Exception:
            pass
    strike_env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Рязань", "Страйк", ".env")
    if os.path.exists(strike_env):
        try:
            with open(strike_env, "r", encoding="utf-8-sig", errors="ignore") as f:
                for line in f:
                    if "=" in line and not line.lstrip().startswith("#"):
                        k, v = line.split("=", 1)
                        if k.strip() == "GEMINI_API_KEY" and v.strip():
                            return v.strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


def _call_gemini_openai_compat(gemini_key, messages, max_tokens=1500, temperature=0.25, timeout=25, json_mode=False):
    for gm in ("gemini-flash-lite-latest", "gemini-3.1-flash-lite-preview", "gemini-flash-latest", "gemini-2.5-flash-lite"):
        body = {"model": gm, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {gemini_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                parsed = json.loads(resp.read().decode("utf-8"))
            txt = (((parsed.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if txt:
                return txt, gm, ""
        except Exception as e:
            last_err = str(e)
    return None, "", last_err if "last_err" in locals() else "gemini_failed"


def call_ai_router(ctx_or_token, messages, fast=False, max_tokens=1400, temperature=0.25, timeout=None, json_mode=False):
    """Tries Gemini (if key configured) -> Local Ollama (auto-detected) -> GitHub Models (strictly JSON-validated)."""
    gemini_key = ""
    gh_token = ""
    if isinstance(ctx_or_token, dict):
        gemini_key = _resolve_runtime_gemini_key(ctx_or_token.get("gemini_api_key"))
        gh_token = str(ctx_or_token.get("gh_token") or "").strip()
    else:
        gemini_key = _resolve_runtime_gemini_key()
        gh_token = str(ctx_or_token or "").strip()

    errors = []

    if gemini_key:
        txt, gm, gerr = _call_gemini_openai_compat(gemini_key, messages, max_tokens=max_tokens, temperature=temperature, timeout=timeout or 25, json_mode=json_mode)
        if txt:
            return txt, f"gemini/{gm}", ""
        errors.append(f"gemini:{gerr}")

    ollama_models = _get_ollama_models()
    omodel = _pick_ollama_model(ollama_models)
    if omodel:
        try:
            ol_to = timeout if timeout else (24 if fast else 75)
            txt = _call_ollama_chat(omodel, messages, max_tokens=max_tokens, temperature=temperature, timeout=ol_to, json_mode=json_mode)
            if txt:
                return txt, f"ollama/{omodel}", ""
        except Exception as oe:
            errors.append(f"ollama({omodel}):{oe}")

    if gh_token:
        txt, ghm, gerr = call_github_models(
            gh_token,
            messages,
            models=FAST_MODELS_CHAIN if fast else DEEP_MODELS_CHAIN,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout or (10 if fast else 30),
            json_mode=json_mode,
        )
        if txt:
            return txt, ghm, ""
        errors.append(f"gh_models:{gerr}")

    return None, "", "; ".join(errors)[:240] or "no_ai_backend_ready"


def call_github_models(gh_token, messages, models=DEEP_MODELS_CHAIN, max_tokens=1500, temperature=0.25, timeout=30, json_mode=False):
    tok = str(gh_token or "").strip()
    if not tok:
        return None, "", "no_gh_token"
    last_err = ""
    for model_id in models:
        body = {
            "model": model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            GH_MODELS_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {tok}",
                "Content-Type": "application/json",
                "User-Agent": "GSG-Director-NLU-34.3",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw_txt = resp.read().decode("utf-8", errors="replace").strip()
            if not raw_txt.startswith("{"):
                last_err = f"Non-JSON response on {model_id}: {raw_txt[:40]}"
                break  # Edge endpoint returned health/plain text; no need to hammer remaining models
            parsed = json.loads(raw_txt)
            txt = (((parsed.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            if txt:
                return txt, model_id, ""
        except urllib.error.HTTPError as he:
            err_body = ""
            try:
                err_body = he.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            last_err = f"HTTP {he.code} on {model_id}: {err_body}"
            continue
        except Exception as e:
            last_err = f"{type(e).__name__} on {model_id}: {e}"
            continue
    return None, "", last_err or "all_models_failed"


def fetch_public_quick_facts(query, lang="ru"):
    """Lightweight public API lookup (DDG Instant Answer JSON + Wikipedia API) with zero CAPTCHA risk."""
    q = re.sub(r"https?://\S+", " ", str(query or "")).strip()[:140]
    if not q:
        return []
    snippets = []
    try:
        u = f"https://api.duckduckgo.com/?q={urllib.parse.quote(q)}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(u, headers={"User-Agent": "GSG-Director-34.3"})
        with urllib.request.urlopen(req, timeout=6) as r:
            obj = json.loads(r.read().decode("utf-8", "replace"))
            if obj.get("AbstractText"):
                snippets.append(f"DDG: {obj.get('Heading') or q} — {obj['AbstractText'][:350]}")
            for rt in (obj.get("RelatedTopics") or [])[:3]:
                if isinstance(rt, dict) and rt.get("Text"):
                    snippets.append(f"• {rt['Text'][:220]}")
    except Exception:
        pass
    if len(snippets) < 2:
        wiki_host = "ru.wikipedia.org" if lang == "ru" else "en.wikipedia.org"
        try:
            wu = f"https://{wiki_host}/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(q[:90])}&format=json&srlimit=2"
            req = urllib.request.Request(wu, headers={"User-Agent": "GSG-Director-34.3"})
            with urllib.request.urlopen(req, timeout=6) as r:
                wobj = json.loads(r.read().decode("utf-8", "replace"))
                for hit in ((wobj.get("query") or {}).get("search") or [])[:2]:
                    snip = re.sub(r"<[^>]+>", "", str(hit.get("snippet") or ""))
                    snippets.append(f"Wiki ({hit.get('title')}): {snip[:240]}")
        except Exception:
            pass
    return snippets[:4]


def _gh_api_get(repo, gh_token, path, timeout=12):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}",
        headers={
            "Authorization": f"token {gh_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "GSG-Director-NLU-34.3",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _gh_post_issue_comment(repo, gh_token, issue_num, body_text):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues/{int(issue_num)}/comments",
        data=json.dumps({"body": body_text}).encode("utf-8"),
        headers={
            "Authorization": f"token {gh_token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "GSG-Director-NLU-34.3",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _extract_issue_project(title, body=""):
    m = re.match(r"^\s*\[([^\]]+)\]", str(title or ""))
    if m:
        return m.group(1).strip()
    pm = re.search(r"\|\s*PROJ:\s*([^|\r\n]+)", str(title or ""), re.IGNORECASE)
    if pm:
        return pm.group(1).strip()
    for pat in (r"Проект:\s*([^\n\r]+)", r"Папка:\s*([^\n\r]+)"):
        bm = re.search(pat, str(body or ""), re.IGNORECASE)
        if bm:
            return bm.group(1).strip()[:60]
    return ""


def _fetch_scoped_tasks_with_comments(ctx, user_obj, is_owner=False, limit=10, with_comments_top=5, specific_issue=None):
    repo = ctx["primary_repo"]
    tok = ctx["gh_token"]
    try:
        raw = _gh_api_get(repo, tok, f"/issues?state=all&sort=created&direction=desc&per_page={limit}")
    except Exception:
        return []
    issues = [x for x in (raw if isinstance(raw, list) else []) if "pull_request" not in x and "[AUTH_SYNC]" not in str(x.get("title") or "")]

    if specific_issue:
        try:
            one = _gh_api_get(repo, tok, f"/issues/{int(specific_issue)}")
            if isinstance(one, dict) and one.get("number") and all(i.get("number") != one.get("number") for i in issues):
                issues.insert(0, one)
        except Exception:
            pass

    lg = str((user_obj or {}).get("login") or "").strip().lower().lstrip("@")
    scoped = []
    for iss in issues:
        proj = _extract_issue_project(iss.get("title"), iss.get("body") or "")
        body_low = str(iss.get("body") or "").lower()
        if is_owner or (lg and lg in body_low) or user_can_see_project(ctx, user_obj, proj, is_owner=False):
            scoped.append({
                "number": iss.get("number"),
                "state": iss.get("state"),
                "title": iss.get("title") or "",
                "project": proj or "Офис",
                "body": (iss.get("body") or "")[:900],
                "created_at": iss.get("created_at") or "",
                "comments_count": iss.get("comments", 0),
                "latest_comment": "",
                "has_real_deliverable": False,
            })

    targets = scoped[:with_comments_top]
    if specific_issue:
        targets = [x for x in scoped if x["number"] == specific_issue][:1] + [x for x in targets if x["number"] != specific_issue][:3]

    for item in targets:
        if item["comments_count"] <= 0:
            continue
        try:
            cs = _gh_api_get(repo, tok, f"/issues/{item['number']}/comments?per_page=10")
            if isinstance(cs, list) and cs:
                best = None
                for c in cs:
                    b = str(c.get("body") or "")
                    if "<!-- director-v34.3-deliverable -->" in b:
                        best = b
                        item["has_real_deliverable"] = True
                if not best:
                    best = str(cs[-1].get("body") or "")
                    if "structured-cloud-analyzer" not in best:
                        item["has_real_deliverable"] = True
                item["latest_comment"] = best[:1100]
        except Exception:
            pass
    return scoped


def _fetch_office_memory_summary(ctx, project_name=""):
    try:
        mem, _ = ctx["fetch_json"](ctx["primary_repo"], ctx["gh_token"], "office_memory.json", branch="data")
    except Exception:
        mem = {}
    if not isinstance(mem, dict):
        return ""
    facts = [f for f in (mem.get("learned_facts") or []) if isinstance(f, dict)]
    lines = []
    proj_low = str(project_name or "").lower()
    prioritized = [
        f for f in facts
        if not proj_low or proj_low in f"{f.get('project') or ''} {f.get('topic') or ''} {f.get('source') or ''}".lower()
    ]
    for f in (prioritized or facts)[-6:]:
        label = f.get("project") or f.get("source") or "Офис"
        iss = f" #{f.get('issue')}" if f.get("issue") else ""
        body = f.get("summary") or f.get("insight") or f.get("topic") or f.get("title") or ""
        if body:
            lines.append(f"- [{label}{iss}]: {str(body)[:260]}")
    try:
        import office_trends_bridge
        tr_snip = office_trends_bridge.get_compact_memory_snippet()
        if tr_snip:
            lines.append(tr_snip)
    except Exception:
        pass
    return "\n".join(lines)


def _persist_bot_work_to_local_and_crm(ctx, issue_num, project_name, goal_text, ans_text, urls=None):
    """Guarantees every Telegram Director answer/task is persisted in Входящие_Офис and crm_data.json."""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    proj = project_name or "Главный Штаб"
    urls = list(urls or [])
    inbox_root = r"C:\Users\Vlaslav\Downloads\Входящие_Офис"
    kb_dir = os.path.join(inbox_root, "04_База_Знаний_Синтез")
    try:
        os.makedirs(kb_dir, exist_ok=True)
        note_name = f"Note_Bot_{issue_num or 'chat'}_{stamp}.md"
        with open(os.path.join(kb_dir, note_name), "w", encoding="utf-8") as nf:
            nf.write(
                f"# Разбор из Telegram-бота ({proj}"
                + (f" · #{issue_num}" if issue_num else "")
                + f") — {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
                f"**Запрос:** {goal_text}\n"
                + (f"**Ссылки:** {', '.join(urls[:3])}\n\n" if urls else "\n")
                + f"---\n\n{ans_text[:4500]}\n"
            )
    except Exception:
        pass

    # Update local _system/board.json when linked to a numeric issue
    if issue_num:
        try:
            board_path = os.path.join(inbox_root, "_system", "board.json")
            if os.path.isfile(board_path):
                with open(board_path, "r", encoding="utf-8-sig") as bf:
                    bd = json.load(bf)
                cards = bd.setdefault("cards", {})
                sid = str(issue_num)
                now_s = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # Avoid colliding with unrelated legacy inbox cards when issue_num matches an existing numeric key
                target_key = next(
                    (k for k, v in cards.items() if isinstance(v, dict) and str(v.get("gh_issue") or "") == sid),
                    None,
                )
                if not target_key:
                    existing = cards.get(sid)
                    if isinstance(existing, dict):
                        ex_urls = set(existing.get("urls") or [])
                        same_url = bool(urls and ex_urls and (set(urls) & ex_urls))
                        same_title = str(goal_text or "")[:30].lower() in str(existing.get("title") or "").lower()
                        target_key = sid if (same_url or same_title or not existing.get("title")) else f"gh-{sid}"
                    else:
                        target_key = sid
                if target_key not in cards or not isinstance(cards[target_key], dict):
                    cards[target_key] = {
                        "id": int(issue_num) if target_key == sid else target_key,
                        "gh_issue": int(issue_num),
                        "created": now_s,
                        "handoffs": [],
                        "title": str(goal_text or "")[:200],
                        "urls": urls,
                        "project": proj,
                        "kind": "link" if urls else "command",
                    }
                c_obj = cards[target_key]
                c_obj["gh_issue"] = int(issue_num)
                c_obj["project"] = proj
                c_obj["status"] = "done"
                c_obj["owner"] = "gatekeeper"
                c_obj["updated"] = now_s
                c_obj["next"] = {"who": "done", "what": "Ответ сохранён в 04_База_Знаний_Синтез и crm_data.json"}
                c_obj.setdefault("handoffs", []).append({"t": now_s, "from": "tg_director", "to": "gatekeeper", "note": "Ответ сохранён в 04_База_Знаний_Синтез и crm_data"})
                with open(board_path, "w", encoding="utf-8") as bf:
                    json.dump(bd, bf, ensure_ascii=False, indent=2)
                def _mut_board(cur):
                    if not isinstance(cur, dict):
                        cur = {"version": 1, "cards": {}}
                    cur.setdefault("cards", {})[target_key] = c_obj
                    return cur
                ctx["mutate_repos"]("board.json", _mut_board, f"Bot issue #{sid} saved to board.json", branches=["data"], default_factory=lambda: {"version": 1, "cards": {}})
        except Exception:
            pass

    # Persist into GitHub data/crm_data.json knowledge_base
    try:
        kb_id = f"kb-bot-{issue_num}" if issue_num else f"kb-bot-{int(time.time())}"
        kb_entry = {
            "id": kb_id,
            "issue": issue_num or "Bot",
            "category": f"Проект: {proj}",
            "title": str(goal_text or ans_text or "Запись из бота")[:140],
            "source_url": urls[0] if urls else "",
            "summary": str(ans_text or "")[:600],
            "updated_at": _utc_now(),
        }
        def _mut_crm(cur):
            if not isinstance(cur, dict):
                cur = {"version": "34.1", "knowledge_base": []}
            kbs = cur.setdefault("knowledge_base", [])
            idx = next((i for i, x in enumerate(kbs) if isinstance(x, dict) and x.get("id") == kb_id), None)
            if idx is not None:
                kbs[idx] = kb_entry
            else:
                kbs.append(kb_entry)
            cur["knowledge_base"] = kbs[-150:]
            cur["updated_at"] = _utc_now()
            return cur
        ctx["mutate_repos"]("crm_data.json", _mut_crm, f"Sync bot KB {kb_id}", branches=["data"], default_factory=dict)
    except Exception:
        pass


def _save_fact_to_office_memory(ctx, issue_num, project_name, summary_text, goal_text="", urls=None, full_answer=""):
    fact_id = f"fact-dir-v343-iss{issue_num}" if issue_num else f"fact-dir-v343-{int(time.time())}"
    entry = {
        "id": fact_id,
        "ts": _utc_now(),
        "project": project_name or "Главный Штаб",
        "issue": issue_num,
        "summary": str(summary_text or "")[:450],
    }

    def _mut(cur):
        if not isinstance(cur, dict):
            cur = {"version": "34.3", "learned_facts": []}
        facts = cur.setdefault("learned_facts", [])
        if not any(isinstance(x, dict) and x.get("id") == fact_id for x in facts):
            facts.append(entry)
            cur["learned_facts"] = facts[-120:]
        cur["updated_at"] = _utc_now()
        return cur

    try:
        ctx["mutate_repos"]("office_memory.json", _mut, f"Director v34.3 memory sync (issue #{issue_num or '-'})", branches=["data"], default_factory=dict)
    except Exception:
        pass
    _persist_bot_work_to_local_and_crm(ctx, issue_num, project_name, goal_text or summary_text, full_answer or summary_text, urls=urls)


# ---------------------------------------------------------------------------
# Bilingual RU/EN NLU Intent Classification
# ---------------------------------------------------------------------------

def classify_director_intent(text, chat_ctx, ctx, user_obj, is_owner=False, gh_token=""):
    clean_text = str(text or "").strip()
    low = clean_text.lower()
    lang = detect_lang(clean_text)
    urls = extract_urls(clean_text)
    cur_proj = chat_ctx.get("active_project") or "Главный Штаб"
    resolved_proj, proj_explicit = resolve_project_mention(clean_text, cur_proj, ctx, user_obj, is_owner=is_owner)

    has_nlm_kw = bool(re.search(r"\b(notebook\s*lm|nlm)\b|ноутбуклм", low))
    issue_ref_m = re.search(r"(?:#|задач[аеиу]?\s*#?)(\d{1,5})", low)
    specific_issue = int(issue_ref_m.group(1)) if issue_ref_m else None

    # 1. Explicit folder switch
    if re.match(r"^(?:/folder|/proj|/папка|переключи\s+папк\w*\s+на|смени\s+папк\w*\s+на|switch\s+folder\s+to)\b", low):
        if proj_explicit:
            return {
                "intent": "switch_folder",
                "lang": lang,
                "project": resolved_proj,
                "urls": urls,
                "specific_issue": None,
                "use_previous_context": False,
                "use_notebooklm": False,
                "needs_background": False,
                "requires_issue": False,
                "understood_goal": f"Переключить активную папку на «{resolved_proj}»",
            }

    # 2. Task status / check new tasks audit query (never create a new task!)
    check_tasks_pat = re.search(
        r"(?:^/tasks\b|провер\w*\s+(?:все\s+)?(?:новые\s+)?задач|какие\s+(?:новые\s+)?задач|что\s+сделал\w*\s+(?:агент|тим|директор|бот)|"
        r"статус\s+(?:задач|работ|офиса|#\d+)|покажи\s+(?:новые\s+)?задач|отч[её]т\s+по\s+задач|"
        r"\bcheck\s+(?:new\s+|recent\s+)?tasks\b|\btask\s+status\b|\bwhat\s+did\s+agents\b)",
        low,
    )
    if check_tasks_pat and not urls and not re.search(r"\b(research|изучи|найди|notebooklm)\b", low):
        goal = f"Проверить задачу #{specific_issue}" if specific_issue else f"Проверить свежие задачи и ответы агентов ({resolved_proj})"
        return {
            "intent": "check_tasks",
            "lang": lang,
            "project": resolved_proj,
            "urls": urls,
            "specific_issue": specific_issue,
            "use_previous_context": False,
            "use_notebooklm": False,
            "needs_background": False,
            "requires_issue": False,
            "understood_goal": goal,
        }

    # 3. Follow-up referencing prior step/link/topic
    follow_pat = re.search(
        r"^(?:выполн\w*\s+шаг|сделай\s+шаг|шаг\s*#?\d+|делай\s+дальше|продолж\w*|давай\s+шаг|"
        r"(?:do|run|execute)\s+step\s*#?\d+|continue|go\s+ahead)\b",
        low,
    ) or (
        re.search(r"\b(это|эту ссылку|previou?s|this link|that link|use notebooklm on this)\b", low)
        and not urls
        and bool(chat_ctx.get("last_urls") or chat_ctx.get("last_issue_number") or chat_ctx.get("last_topic"))
    )
    if follow_pat:
        return {
            "intent": "follow_up_step",
            "lang": lang,
            "project": resolved_proj,
            "urls": urls or list(chat_ctx.get("last_urls") or []),
            "specific_issue": specific_issue or chat_ctx.get("last_issue_number"),
            "use_previous_context": True,
            "use_notebooklm": has_nlm_kw,
            "needs_background": True,
            "requires_issue": False,
            "understood_goal": clean_text[:180],
        }

    # 4. Research / URL / NotebookLM explicit task
    research_pat = bool(urls or has_nlm_kw or re.search(
        r"(?:найди\s+(?:платформ|сервис|инструмент|вариант|решени)|изучи|исследуй|проанализируй|как\s+(?:нам\s+)?применить|"
        r"подготовь\s+план|разбери|\bfind\s+a?\s*platform|\bresearch\b|\bstudy\b|\banalyze\b|\binvestigate\b)",
        low,
    ))
    if research_pat:
        return {
            "intent": "notebooklm_research",
            "lang": lang,
            "project": resolved_proj,
            "urls": urls,
            "specific_issue": specific_issue,
            "use_previous_context": bool(chat_ctx.get("last_urls") and ("this" in low or "эт" in low)),
            "use_notebooklm": has_nlm_kw,
            "needs_background": True,
            "requires_issue": True,
            "understood_goal": clean_text[:200],
        }

    # 5. Capability / understanding check -> answer directly without junk issue
    if re.search(r"(?:ты\s+(?:меня\s+)?понимаеш|как\s+ты\s+работаеш|что\s+ты\s+умееш|кто\s+ты|do\s+you\s+understand|what\s+can\s+you\s+do)", low):
        return {
            "intent": "direct_chat",
            "lang": lang,
            "project": resolved_proj,
            "urls": urls,
            "specific_issue": specific_issue,
            "use_previous_context": False,
            "use_notebooklm": False,
            "needs_background": False,
            "requires_issue": False,
            "understood_goal": clean_text[:180],
        }

    # 6. LLM NLU Classification via GitHub Models (gpt-4.1-mini) with recent conversation history
    recent_turns = []
    for t in (chat_ctx.get("turns") or [])[-4:]:
        recent_turns.append(f"{t.get('role')}: {str(t.get('text') or '')[:160]} (proj={t.get('project')}, iss=#{t.get('issue_number')})")
    history_str = "\n".join(recent_turns) or "Пусто"

    sys_prompt = (
        "Ты — NLU-классификатор Личного ИИ-Директора Тима для Global Spirit Group (RU/EN).\n"
        "Определи намерение сообщения пользователя. Верни ТОЛЬКО JSON со схемой:\n"
        "{\n"
        '  "intent": "check_tasks" | "follow_up_step" | "notebooklm_research" | "task_create" | "direct_chat" | "switch_folder",\n'
        '  "project": "строка папки или текущая",\n'
        '  "use_previous_context": boolean,\n'
        '  "use_notebooklm": boolean,\n'
        '  "needs_background": boolean,\n'
        '  "requires_issue": boolean,\n'
        '  "understood_goal": "1 короткое предложение на языке пользователя, что именно понял Директор"\n'
        "}\n"
        "Правила:\n"
        "- Вопросы про уже поставленные задачи или статус -> check_tasks (requires_issue=false).\n"
        "- Команды 'сделай это', уточнение прошлого запроса или привязка к прошлой ссылке -> use_previous_context=true, follow_up_step.\n"
        "- Исследование/ссылка/поиск платформы/плана/NotebookLM -> notebooklm_research (needs_background=true, requires_issue=true).\n"
        "- Конкретное поручение 'создай/поставь/подготовь' -> task_create (needs_background=true, requires_issue=true).\n"
        "- Короткий вопрос/беседа без постановки внешней задачи -> direct_chat (requires_issue=false)."
    )
    user_prompt = (
        f"Текущая папка: {cur_proj}\n"
        f"Последняя задача: #{chat_ctx.get('last_issue_number')} ({chat_ctx.get('last_topic')})\n"
        f"Прошлые ссылки: {chat_ctx.get('last_urls')}\n"
        f"История:\n{history_str}\n\n"
        f"Новое сообщение: {clean_text}"
    )
    raw_json, _, _ = call_ai_router(
        ctx if (gh_token or (ctx or {}).get("gemini_api_key") or _get_ollama_models()) else gh_token,
        [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}],
        fast=True,
        max_tokens=320,
        temperature=0.05,
        timeout=14,
        json_mode=True,
    )
    if raw_json:
        try:
            obj = json.loads(raw_json)
            intent = str(obj.get("intent") or "task_create")
            if intent not in ("check_tasks", "follow_up_step", "notebooklm_research", "task_create", "direct_chat", "switch_folder"):
                intent = "task_create"
            cand_proj = str(obj.get("project") or resolved_proj).strip() or resolved_proj
            cand_proj, _ = resolve_project_mention(cand_proj, resolved_proj, ctx, user_obj, is_owner=is_owner)
            use_prev = bool(obj.get("use_previous_context"))
            eff_urls = urls or (list(chat_ctx.get("last_urls") or []) if use_prev else [])
            return {
                "intent": intent,
                "lang": lang,
                "project": cand_proj,
                "urls": eff_urls,
                "specific_issue": specific_issue or (chat_ctx.get("last_issue_number") if use_prev else None),
                "use_previous_context": use_prev,
                "use_notebooklm": bool(obj.get("use_notebooklm")) or has_nlm_kw,
                "needs_background": bool(obj.get("needs_background")) or intent in ("notebooklm_research", "task_create", "follow_up_step"),
                "requires_issue": bool(obj.get("requires_issue")) and intent in ("notebooklm_research", "task_create"),
                "understood_goal": str(obj.get("understood_goal") or clean_text[:180])[:220],
            }
        except Exception:
            pass

    # Deterministic safe fallback
    is_action_cmd = bool(re.search(r"\b(сделай|создай|запусти|напиши|подготовь|настрой|create|build|make|prepare|launch|setup)\b", low))
    intent = "task_create" if is_action_cmd or len(clean_text) > 75 else "direct_chat"
    return {
        "intent": intent,
        "lang": lang,
        "project": resolved_proj,
        "urls": urls,
        "specific_issue": specific_issue,
        "use_previous_context": False,
        "use_notebooklm": has_nlm_kw,
        "needs_background": intent == "task_create",
        "requires_issue": intent == "task_create",
        "understood_goal": clean_text[:180],
    }


# ---------------------------------------------------------------------------
# Synchronous Fast Handlers (check_tasks, direct_chat) + Async Worker
# ---------------------------------------------------------------------------

def _build_check_tasks_reply(ctx, user_obj, is_owner, nlu, dir_name, proj, gh_token):
    tasks = _fetch_scoped_tasks_with_comments(
        ctx, user_obj, is_owner=is_owner, limit=12, with_comments_top=6, specific_issue=nlu.get("specific_issue")
    )
    if not tasks:
        return (
            f"📋 <b>Директор «{_esc(dir_name)}» · Проверка задач</b>\n\n"
            f"В вашем контуре (<code>{_esc(proj)}</code>) пока нет доступных задач в GitHub Issues."
        ), []

    # Fast deterministic fallback lines
    lines = [f"📋 <b>Директор «{_esc(dir_name)}» — Разбор свежих задач ({len(tasks[:6])})</b>\n"]
    kb_row = []
    compact_for_ai = []
    for t in tasks[:5]:
        st_ico = "✅" if t["state"] == "closed" else ("🧠" if t["has_real_deliverable"] else "🟡")
        title_clean = re.sub(r"^CMD:\s*(?:TIM:\s*\[[^\]]+\]\s*)?", "", t["title"]).split("| PROJ:")[0].strip()
        ans_tag = "есть разбор" if t["has_real_deliverable"] else ("только авто-заглушка" if t["comments_count"] else "ждёт разбора")
        lines.append(f"{st_ico} <b>#{t['number']}</b> [{_esc(t['project'])}] — {_esc(title_clean[:78])}\n    <i>Статус: {ans_tag}</i>")
        if len(kb_row) < 3:
            kb_row.append({"text": f"🔍 #{t['number']}", "callback_data": f"dact:deep:{t['number']}"})
        compact_for_ai.append(
            f"Issue #{t['number']} ({t['state']}, project={t['project']}, has_real_deliverable={t['has_real_deliverable']}):\n"
            f"Title: {t['title']}\nBody: {t['body'][:300]}\nLatestComment: {t['latest_comment'][:500]}\n"
        )

    lang = nlu.get("lang") or "ru"
    if gh_token:
        sys_p = (
            f"Ты — Личный ИИ-Директор «{dir_name}» в Global Spirit Group.\n"
            f"Язык ответа: {'English' if lang == 'en' else 'Русский'}.\n"
            "Проанализируй переданные реальные GitHub Issues и комментарии агентов.\n"
            "Строго говори ТОЛЬКО о фактах из переданных данных. Покажи по каждой важной задаче: "
            "1) что просил пользователь, 2) что реально уже сделано/найдено (или честно скажи, если была только заглушка/нет фото/нет авторизации nlm), 3) конкретный следующий шаг.\n"
            "Пиши живо, кратко, структурированно (до 1800 символов, можно Markdown **жирный** и списки)."
        )
        ai_txt, model_used, _ = call_ai_router(
            ctx,
            [{"role": "system", "content": sys_p}, {"role": "user", "content": "\n---\n".join(compact_for_ai)}],
            fast=True,
            max_tokens=900,
            temperature=0.2,
            timeout=20,
        )
        if ai_txt:
            return f"👑 **Директор «{dir_name}» · Живой аудит задач** (`{model_used}`)\n\n{ai_txt}", kb_row

    return "\n".join(lines), kb_row


def _get_user_director_persona(login, project_name=""):
    lg = str(login or "").strip().lower().lstrip("@")
    proj_low = str(project_name or "").strip().lower()
    if lg in ("aliashirani", "alberta_shirani", "alberta") or "alberta" in proj_low or "альберт" in proj_low:
        return (
            "Специализация: направление Alberta Shirani (`Alberta` / `Личный Офис: @aliashirani`).\n"
            "Ключевые проекты: Закрытый женский клуб 30–50 лет «Трансформация» (опора, уверенность, стиль, доход, отношения, снятие тревожности) + VIP 99k–150k ₽, "
            "мини-продукт «Возврат Опоры за 72 часа», Telegram-воронка подписки (ЮKassa/Prodamus), хабы alberta-hub и Международный Штаб 5-в-1, 7 Reels / VEO 3.1."
        )
    if lg in ("yvyatkin", "jv", "yura") or proj_low == "юра":
        return (
            "Специализация: направление Юрия Вяткина (`Юра` / `Личный Офис: @yvyatkin`).\n"
            "Ключевые проекты: аналитика и VIP-канал прогнозов (отбор событий, критерий Келли, авто-подписка в Telegram), AI-криптомониторинг импульсов BTC/ETH/SOL (алерты UptrendDipVol), "
            "облачные ИИ-воронки и автоматизация отчётов (старые локальные ПК-воркеры KHL/Freqtrade на паузе)."
        )
    return f"Специализация: контур «{project_name or 'Офис'}» Global Spirit Group."


def get_personal_start_greeting(user_obj, default_proj="Мой Офис"):
    login = str((user_obj or {}).get("login") or "").strip().lower().lstrip("@")
    pb = (user_obj or {}).get("personal_bot") or {}
    dir_name = pb.get("director_name") or "Тим"
    full_name = user_obj.get("full_name") or login
    if login in ("aliashirani", "alberta_shirani", "alberta"):
        focus = (
            "• 🎯 <b>Ваши проекты:</b> Закрытый клуб 30–50 «Трансформация», мини-продукт, Telegram-воронка подписки, хаб <code>alberta-hub</code> и контент Reels/VEO.\n"
            "• 💡 Напишите любую задачу (например: <i>«сделай оффер для клуба», «проверь задачи», «разбери ссылку»</i>) — сразу подключу весь Офис."
        )
    elif login in ("yvyatkin", "jv", "yura"):
        focus = (
            "• 🎯 <b>Ваш контур «Юра»:</b> VIP-канал и аналитика, AI-криптомониторинг импульсов/алертов, Telegram-воронки и облачные агенты 24/7.\n"
            "• 💡 Напишите любую задачу (например: <i>«проверь мои задачи», «идеи роста VIP-подписки», «разбери ссылку»</i>) — сразу запущу в работу."
        )
    else:
        focus = "• 💬 <i>Выберите действие кнопкой ниже или напишите любую задачу, вопрос или ссылку:</i>"
    return (
        f"👑 <b>Здравствуйте, {_esc(full_name)}!</b>\n"
        f"Я — ваш Личный AI-Директор <b>«{_esc(dir_name)}»</b> (активирован Главным Штабом @unitybusinessbot).\n\n"
        f"📁 Активная папка: <b>«{_esc(default_proj)}»</b>\n"
        f"{focus}"
    )


def _handle_direct_chat_sync(ctx, user_obj, is_owner, nlu, clean_text, chat_ctx, dir_name, proj, bot_key, chat_id, bot_token, bot_label, reply_markup, gh_token):
    lang = nlu.get("lang") or "ru"
    mem_facts = _fetch_office_memory_summary(ctx, proj)
    nlm_st = check_nlm_status()
    persona_ctx = _get_user_director_persona((user_obj or {}).get("login"), proj)
    recent = []
    for t in (chat_ctx.get("turns") or [])[-6:]:
        recent.append({"role": "user" if t.get("role") == "user" else "assistant", "content": str(t.get("text") or "")[:500]})

    sys_p = (
        f"Ты — Личный ИИ-Директор «{dir_name}» в Global Spirit Group ★ Виртуальный Офис v34.3.\n"
        f"Роль пользователя: {'Владелец Главного Штаба (@unityspiritpartners)' if is_owner else '@' + str((user_obj or {}).get('login') or '')}.\n"
        f"{persona_ctx}\n"
        f"Активная папка: {proj}. Текущая задача в контексте: #{chat_ctx.get('last_issue_number')} ({chat_ctx.get('last_topic')}).\n"
        f"Статус локального NotebookLM (`nlm`): {'Авторизован' if nlm_st.get('authenticated') else 'Установлен, но не авторизован (нужен 1x `nlm login`)'}.\n"
        f"Память Офиса:\n{mem_facts or 'Нет недавних записей.'}\n\n"
        f"Отвечай на {'English' if lang == 'en' else 'Русском'} языке точно, по делу, без канцелярита и шаблонов. "
        "Если пользователь просит проверить или объяснить — отвечай конкретно по существу, не создавая лишних тикетов."
    )
    msgs = [{"role": "system", "content": sys_p}] + recent + [{"role": "user", "content": clean_text}]
    ai_txt, model_used, err = call_ai_router(ctx, msgs, fast=True, max_tokens=900, temperature=0.35, timeout=22)

    if not ai_txt:
        last_prev = str(chat_ctx.get("last_answer_preview") or "").strip()
        ai_txt = (
            f"👑 **Директор «{dir_name}» (папка «{proj}»)**\n\n"
            f"Понял запрос: *{nlu.get('understood_goal') or clean_text[:140]}*\n"
            f"• Контекст: задача `#{chat_ctx.get('last_issue_number') or '—'}` ({chat_ctx.get('last_topic') or 'свободный диалог'})\n"
            + (f"• Недавний факт из памяти Офиса:\n{mem_facts[:400]}\n" if mem_facts else "")
            + (f"\n• Последний разбор: {last_prev[:250]}...\n" if last_prev else "")
            + "\nНапишите `/tasks` для живого аудита задач или пришлите ссылку/вопрос для детального разбора."
        )
    else:
        ai_txt = f"👑 **Директор «{dir_name}»** (`{model_used}`) · _{proj}_\n\n{ai_txt}"

    update_chat_context(
        bot_key,
        chat_id,
        active_project=proj,
        last_answer_preview=ai_txt[:350],
        turn_entry={"ts": _utc_now(), "role": "assistant", "text": ai_txt[:450], "intent": "direct_chat", "project": proj},
    )
    send_tg_smart(ctx, bot_token, chat_id, ai_txt, reply_markup=reply_markup, bot_label=bot_label, as_markdown=True)


def _run_background_execution(
    ctx,
    user_obj,
    is_owner,
    nlu,
    clean_text,
    chat_ctx,
    dir_name,
    proj,
    bot_key,
    chat_id,
    bot_token,
    bot_label,
    issue_num,
    gh_token,
    reply_markup_fn,
):
    exec_key = f"{bot_key}:{chat_id}:{issue_num or 'tmp'}:{hash(clean_text[:80])}"
    with _ACTIVE_EXEC_LOCK:
        prev_ts = _ACTIVE_EXEC_KEYS.get(exec_key, 0)
        if prev_ts and (time.time() - prev_ts) < 25:
            return
        _ACTIVE_EXEC_KEYS[exec_key] = time.time()

    try:
        urls = list(nlu.get("urls") or [])
        url_findings = []
        for u in urls[:3]:
            meta = fetch_url_metadata_safe(u, timeout=9)
            url_findings.append(meta)

        nlm_res = {"used": False, "status": check_nlm_status()}
        if nlu.get("use_notebooklm"):
            nlm_res = try_nlm_research(clean_text, urls=urls, topic=proj)

        mem_summary = _fetch_office_memory_summary(ctx, proj)
        issue_context = ""
        if issue_num:
            try:
                iss = _gh_api_get(ctx["primary_repo"], gh_token, f"/issues/{int(issue_num)}")
                cs = _gh_api_get(ctx["primary_repo"], gh_token, f"/issues/{int(issue_num)}/comments?per_page=6")
                c_txt = "\n---\n".join(str(c.get("body") or "")[:500] for c in (cs if isinstance(cs, list) else [])[-2:])
                issue_context = f"Issue #{issue_num}: {iss.get('title')}\n{str(iss.get('body') or '')[:800]}\nPrevComments:\n{c_txt}"
            except Exception:
                pass

        lang = nlu.get("lang") or "ru"
        nlm_status_txt = (
            f"NotebookLM вернул ответ (notebook_id={nlm_res.get('notebook_id')}):\n{nlm_res.get('answer')}"
            if nlm_res.get("used")
            else f"NotebookLM CLI не использован: {nlm_res.get('status', {}).get('reason', 'не авторизован')}"
        )

        urls_block = json.dumps(url_findings, ensure_ascii=False, indent=2)[:2800] if url_findings else "Ссылки не переданы"
        prev_topic = chat_ctx.get("last_topic") or ""
        persona_ctx = _get_user_director_persona((user_obj or {}).get("login"), proj)
        sys_p = (
            f"Ты — «{dir_name}», Личный ИИ-Директор в Global Spirit Group ★ Виртуальный Офис (v34.3).\n"
            f"Язык ответа: {'English' if lang == 'en' else 'Русский'}.\n"
            f"Папка: {proj}. Задача #{issue_num or 'в контексте'}.\n"
            f"{persona_ctx}\n"
            "КРИТИЧЕСКИЕ ПРАВИЛА:\n"
            "1. Дай КОНКРЕТНЫЙ, глубокий и сразу применимый ответ по сути запроса пользователя — НИКАКИХ отписок 'принял поручение'.\n"
            "2. Ничего не выдумывай про содержимое видео/страниц, если в извлечённых данных URL этого нет: честно укажи, что именно удалось прочитать по ссылке (Final URL, og:title, og:description), а по самому запросу пользователя дай проверенную экспертизу и инструменты.\n"
            "3. Если пользователь просил NotebookLM, а профиль `nlm` не авторизован — прямо в 1 строке отметь, что нужно 1 раз выполнить `nlm login` на ПК, и дай полноценный разбор силами Gemini / локальной ИИ-модели + Памяти Офиса.\n"
            "4. В конце предложи 1–3 чётких следующих шага применительно к контуру пользователя/бизнеса (без воды)."
        )
        user_p = (
            f"Запрос пользователя: {clean_text}\n"
            f"Понимание цели: {nlu.get('understood_goal')}\n"
            f"Предыдущая тема в диалоге: {prev_topic}\n\n"
            f"### Извлечённые данные по ссылкам:\n{urls_block}\n\n"
            f"### Статус NotebookLM:\n{nlm_status_txt}\n\n"
            f"### Контекст Issue #{issue_num or '-'}:\n{issue_context[:1400]}\n\n"
            f"### Релевантная Память Офиса:\n{mem_summary or 'Пусто'}"
        )

        quick_facts = fetch_public_quick_facts(clean_text, lang=lang)
        facts_block = "\n".join(quick_facts) if quick_facts else ""

        ans_txt, model_used, err = call_ai_router(
            ctx,
            [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p + (f"\n\n### Справочные факты сети:\n{facts_block}" if facts_block else "")}],
            fast=False,
            max_tokens=1600,
            temperature=0.25,
            timeout=65,
        )
        if not ans_txt:
            parts_fb = [f"**Понятая цель:** {nlu.get('understood_goal')}"]
            if url_findings:
                parts_fb.append("### Извлечённые данные по ссылкам:")
                for uf in url_findings:
                    if uf.get("ok"):
                        parts_fb.append(f"• `{uf.get('final_url') or uf.get('url')}` — **{uf.get('title') or 'Без заголовка'}**\n  {uf.get('description') or uf.get('text_excerpt', '')[:260]}")
                    else:
                        parts_fb.append(f"• `{uf.get('url')}` — не удалось прочитать напрямую ({uf.get('error')})")
            if nlm_res.get("used"):
                parts_fb.append(f"### Ответ NotebookLM:\n{nlm_res.get('answer')[:1200]}")
            elif nlu.get("use_notebooklm"):
                parts_fb.append(f"### Статус NotebookLM:\n• {nlm_res.get('status', {}).get('reason')}")
            if mem_summary:
                parts_fb.append(f"### Релевантные знания из Памяти Офиса:\n{mem_summary[:800]}")
            if facts_block:
                parts_fb.append(f"### Быстрая справка:\n{facts_block}")
            parts_fb.append(f"\n*Примечание: внешние LLM-ключи не заданы или локальная модель ещё загружается; при наличии `qwen2.5:3b` в Ollama или `GEMINI_API_KEY` ответ синтезируется полностью нейросетью.*")
            ans_txt = "\n\n".join(parts_fb)
            model_used = "office-rag-and-url-analyzer"

        header_line = f"👑 **Ответ Директора «{dir_name}»** · Папка: `{proj}`"
        if issue_num:
            header_line += f" · Задача `#{issue_num}`"
        header_line += f" · `{model_used}`"
        full_tg = f"{header_line}\n\n{ans_txt}"

        # Persist to GitHub Issue + Office Memory
        if issue_num:
            try:
                gh_comment = f"<!-- director-v34.3-deliverable -->\n<!-- cloud-agent-v34 -->\n{full_tg}"
                _gh_post_issue_comment(ctx["primary_repo"], gh_token, issue_num, gh_comment)
            except Exception:
                pass
            _save_fact_to_office_memory(ctx, issue_num, proj, f"{nlu.get('understood_goal')}: {ans_txt[:280]}", goal_text=clean_text, urls=urls, full_answer=ans_txt)
        else:
            _persist_bot_work_to_local_and_crm(ctx, None, proj, clean_text, ans_txt, urls=urls)

        update_chat_context(
            bot_key,
            chat_id,
            active_project=proj,
            last_issue_number=issue_num or chat_ctx.get("last_issue_number"),
            last_topic=clean_text[:200],
            last_urls=urls or chat_ctx.get("last_urls") or [],
            last_answer_preview=ans_txt[:450],
            turn_entry={
                "ts": _utc_now(),
                "role": "assistant",
                "text": ans_txt[:500],
                "intent": nlu.get("intent"),
                "project": proj,
                "issue_number": issue_num,
                "urls": urls,
            },
        )
        try:
            ctx["record_action"](user_obj, f"nlu_{nlu.get('intent')}", proj, f"Исполнен запрос (#{issue_num or '-'}): {clean_text[:180]}")
        except Exception:
            pass

        extra_row = None
        if issue_num:
            extra_row = [
                {"text": f"🔬 Копнуть #{issue_num} глубже", "callback_data": f"dact:deep:{issue_num}"},
                {"text": "📋 Все свежие задачи", "callback_data": f"dact:tasks:{proj[:20]}"},
            ]
        kb = reply_markup_fn(proj, extra_row=extra_row)
        send_tg_smart(ctx, bot_token, chat_id, full_tg, reply_markup=kb, bot_label=bot_label, as_markdown=True)
    finally:
        with _ACTIVE_EXEC_LOCK:
            _ACTIVE_EXEC_KEYS.pop(exec_key, None)


# ---------------------------------------------------------------------------
# Public Entrypoints Called by tg_bot_poller.py
# ---------------------------------------------------------------------------

def handle_director_text(
    ctx,
    user_obj,
    chat_id,
    text,
    is_owner=False,
    default_project="Главный Штаб",
    bot_token="",
    bot_label="main_hq",
    reply_markup_fn=None,
):
    clean_text = str(text or "").strip()
    if not clean_text:
        return True

    pb = (user_obj or {}).get("personal_bot") or {}
    dir_name = pb.get("director_name") or "Тим"
    login = str((user_obj or {}).get("login") or "user").strip().lower().lstrip("@")
    gh_token = ctx.get("gh_token") or ""
    tok = bot_token or ctx["tg_bot_token"]
    bot_key = "main_hq" if tok == ctx["tg_bot_token"] else (pb.get("bot_username") or login)

    if reply_markup_fn is None:
        reply_markup_fn = lambda proj_val, extra_row=None: {"inline_keyboard": []}

    chat_ctx = get_chat_context(bot_key, chat_id, default_project=default_project)
    nlu = classify_director_intent(clean_text, chat_ctx, ctx, user_obj, is_owner=is_owner, gh_token=gh_token)
    proj = nlu.get("project") or chat_ctx.get("active_project") or default_project or "Главный Штаб"
    intent = nlu.get("intent") or "task_create"

    # Record user turn immediately so rapid follow-ups (like Step #1 20s later) retain full context
    update_chat_context(
        bot_key,
        chat_id,
        active_project=proj,
        last_urls=nlu.get("urls") or chat_ctx.get("last_urls") or [],
        last_topic=clean_text[:200] if intent != "follow_up_step" else (chat_ctx.get("last_topic") or clean_text[:200]),
        turn_entry={
            "ts": _utc_now(),
            "role": "user",
            "text": clean_text[:500],
            "intent": intent,
            "project": proj,
            "issue_number": nlu.get("specific_issue") or chat_ctx.get("last_issue_number"),
            "urls": nlu.get("urls") or [],
        },
    )

    if intent == "switch_folder":
        msg = f"📁 <b>Директор «{_esc(dir_name)}»:</b> активная папка переключена на <b>«{_esc(proj)}»</b>.\n\n<i>Теперь все запросы и анализ привязаны к «{_esc(proj)}». Пишите любую задачу!</i>"
        send_tg_smart(ctx, tok, chat_id, msg, reply_markup=reply_markup_fn(proj), bot_label=bot_label, as_markdown=False)
        return True

    if re.search(r"(?:^/trends\b|\bmoney\s*trends?\b|\bfacebook\s*trends?\b|\bdaily\s*trends?\b|тренды\s+(?:дохода|заработка|рынка|дня)|на\s+ч[её]м\s+заработать\s+сегодня)", clean_text, re.IGNORECASE):
        try:
            import office_trends_bridge
            brief = office_trends_bridge.format_briefing_for_telegram()
            header = f"👑 <b>Директор «{_esc(dir_name)}» · Live Money Trend Radar</b>\n\n"
            send_tg_smart(ctx, tok, chat_id, header + brief, reply_markup=reply_markup_fn(proj), bot_label=bot_label, as_markdown=False)
            update_chat_context(bot_key, chat_id, last_answer_preview=(header + brief)[:350])
            return True
        except Exception:
            pass

    if re.search(r"(?:^/seo\b|\bseo[- ]?(?:аудит|проверк|анализ|задач|скилл|audit|check|tasks?|autopilot)\b|(?:проверь|запусти|сделай|выполни)\s+seo\b)", clean_text, re.IGNORECASE):
        try:
            import office_seo_worker

            urls_seo = nlu.get("urls") or extract_urls(clean_text)
            q_task = office_seo_worker.queue_seo_task(clean_text, url=urls_seo[0] if urls_seo else "", priority="high", source=f"director_{login}")
            ack_seo = (
                f"🔎 <b>Директор «{_esc(dir_name)}» запустил Claude SEO-Агент (Задача <code>{_esc(q_task['id'])}</code>)</b>\n\n"
                + office_seo_worker.format_status_for_telegram()
                + "\n\n<i>⏳ Выполняю SEO-проверку и задачи прямо сейчас в фоне...</i>"
            )
            send_tg_smart(ctx, tok, chat_id, ack_seo, reply_markup=reply_markup_fn(proj), bot_label=bot_label, as_markdown=False)

            def _dir_seo_job():
                try:
                    office_seo_worker.run_seo_cycle(
                        trigger=f"director_{login}",
                        force=True,
                        notify=True,
                        single_url=urls_seo[0] if (urls_seo and len(clean_text.split()) <= 4) else "",
                    )
                except Exception:
                    pass

            threading.Thread(target=_dir_seo_job, daemon=True).start()
            update_chat_context(bot_key, chat_id, last_answer_preview=ack_seo[:350])
            return True
        except Exception:
            pass

    if re.search(r"(?:^/store\b|создай\s+(?:ai[- ]?)?магазин|собери\s+(?:ai[- ]?)?магазин|сделай\s+витрину|build\s+(?:my\s+)?store|open\s*v3\s*store)", clean_text, re.IGNORECASE):
        try:
            import store_builder_engine
            niche = re.sub(r"^(?:/store|создай\s+(?:ai[- ]?)?магазин|собери\s+(?:ai[- ]?)?магазин|сделай\s+витрину|build\s+(?:my\s+)?store|open\s*v3\s*store)\s*", "", clean_text, flags=re.IGNORECASE).strip() or "Smart Tech & Lifestyle"
            built = store_builder_engine.build_and_persist_store(
                {
                    "first_name": (user_obj or {}).get("full_name") or login,
                    "email": f"{login}@globalspirit.group",
                    "phone": f"@{login}",
                    "niche": niche,
                    "language": "en" if re.search(r"\b(?:build|store|english)\b", clean_text, re.IGNORECASE) and not re.search(r"[а-яА-ЯёЁ]", clean_text) else "ru",
                    "utm_source": f"tg_director_{bot_key}",
                },
                sync_github=True,
                notify_telegram=False,
            )
            sp = built["bundle"]["spec"]
            kit = built["bundle"]["traffic_kit"]
            msg_out = (
                f"🛍️ <b>OPEN V3 AI-МАГАЗИН СОБРАН ({_esc(sp['store_id'])})!</b>\n\n"
                f"• <b>Бренд:</b> {_esc(sp['brand_name'])} (<i>{_esc(sp['niche'])}</i>)\n"
                f"• <b>Движок:</b> <code>{_esc(str(sp['engine_used']))}</code>\n"
                f"• <b>Товаров:</b> {len(sp['products'])} SKU | Промокод: <code>{sp['promo_code']}</code>\n"
                f"• <b>Воронка дохода:</b> Shopify CPA $150 + Pro $29/мес + DFY $750\n"
                f"• <b>Папка с файлами на ПК:</b> <code>{_esc(built['saved_dir'])}</code>\n\n"
                f"🎬 <b>Хук рекламы:</b>\n<code>{_esc(kit['vsl_script_45s'][:240])}</code>"
            )
            kb = {
                "inline_keyboard": [
                    [{"text": "🛍️ Открыть Web-билдер Open V3", "url": store_builder_engine.OPEN_V3_WEB_URL}],
                    [{"text": "💰 Выручка и Лиды Офиса", "callback_data": "cmd:revenue"}],
                ]
            }
            send_tg_smart(ctx, tok, chat_id, msg_out, reply_markup=kb, bot_label=bot_label, as_markdown=False)
            if callable(ctx.get("send_document")):
                out_p = built["saved_dir"]
                for fn in ("standalone_storefront.html", "shopify_products.csv"):
                    fp = os.path.join(out_p, fn)
                    if os.path.exists(fp):
                        ctx["send_document"](chat_id, fp, caption=f"🛍️ {sp['brand_name']} — {fn}", bot_token=tok)
            update_chat_context(bot_key, chat_id, last_answer_preview=msg_out[:350])
            return True
        except Exception as _st_err:
            pass

    if intent == "check_tasks":
        ans_md, kb_row = _build_check_tasks_reply(ctx, user_obj, is_owner, nlu, dir_name, proj, gh_token)
        kb = reply_markup_fn(proj, extra_row=kb_row if kb_row else None)
        send_tg_smart(ctx, tok, chat_id, ans_md, reply_markup=kb, bot_label=bot_label, as_markdown=True)
        update_chat_context(bot_key, chat_id, last_answer_preview=ans_md[:350])
        return True

    if intent == "direct_chat":
        _handle_direct_chat_sync(
            ctx, user_obj, is_owner, nlu, clean_text, chat_ctx, dir_name, proj,
            bot_key, chat_id, tok, bot_label, reply_markup_fn(proj), gh_token,
        )
        return True

    # Follow-up OR Research OR Task Execution -> create issue if needed, ack immediately, execute in background!
    issue_num = nlu.get("specific_issue") or (chat_ctx.get("last_issue_number") if intent == "follow_up_step" else None)
    eff_text = clean_text
    if intent == "follow_up_step":
        prev_t = chat_ctx.get("last_topic") or ""
        prev_u = ", ".join(chat_ctx.get("last_urls") or [])
        eff_text = f"{clean_text} — контекст: {prev_t}" + (f" ({prev_u})" if prev_u else "")

    if nlu.get("requires_issue") or not issue_num:
        create_fn = ctx.get("create_issue_full")
        if callable(create_fn):
            body_text = (
                f"Отправлено @{login} через бота (Директор: {dir_name}):\n\n{clean_text}\n\n"
                f"<!-- NLU_GOAL: {nlu.get('understood_goal')} -->\n<!-- ACTOR: {login} -->"
            )
            issue_num = create_fn(proj, f"TIM: [{dir_name}] {clean_text[:115]}", body_text)
        if issue_num:
            update_chat_context(bot_key, chat_id, last_issue_number=issue_num, last_issue_title=clean_text[:120])

    # Instant crystal-clear acknowledgment proving comprehension
    urls_found = nlu.get("urls") or []
    nlm_note = ""
    active_ollama = _pick_ollama_model(_get_ollama_models())
    engine_label = (
        "Gemini + Память Офиса"
        if (ctx or {}).get("gemini_api_key")
        else (f"Ollama ({active_ollama}) + ссылки + Память Офиса" if active_ollama else "Живой разбор ссылок + Память Офиса + OpenFacts")
    )
    if nlu.get("use_notebooklm"):
        st = check_nlm_status()
        nlm_note = (
            "\n• 📓 <b>NotebookLM CLI:</b> запускаю запрос в блокнот"
            if st.get("authenticated")
            else f"\n• 📓 <b>NotebookLM CLI:</b> профиль на ПК не авторизован (нужен 1x <code>nlm login</code>), поэтому сразу делаю разбор через <b>{_esc(engine_label)}</b>"
        )
    iss_badge = f" · Задача <b>#{issue_num}</b>" if issue_num else ""
    urls_badge = f"\n• 🔗 <b>Ссылки:</b> <code>{_esc(', '.join(urls_found[:2]))}</code>" if urls_found else ""
    ack_html = (
        f"🧠 <b>Директор «{_esc(dir_name)}» понял запрос{iss_badge}</b>\n\n"
        f"• 🎯 <b>Цель:</b> {_esc(nlu.get('understood_goal') or clean_text[:180])}\n"
        f"• 📁 <b>Папка:</b> «{_esc(proj)}»"
        f"{urls_badge}"
        f"{nlm_note}\n\n"
        f"<i>⏳ Выполняю глубокий разбор и проверку фактов в фоне — пришлю готовый ответ сюда через 5–15 сек...</i>"
    )
    send_tg_smart(ctx, tok, chat_id, ack_html, reply_markup=reply_markup_fn(proj), bot_label=bot_label, as_markdown=False)

    th = threading.Thread(
        target=_run_background_execution,
        args=(
            ctx, user_obj, is_owner, nlu, eff_text, chat_ctx, dir_name, proj,
            bot_key, chat_id, tok, bot_label, issue_num, gh_token, reply_markup_fn,
        ),
        daemon=True,
    )
    th.start()
    return True


def handle_director_callback(
    ctx,
    user_obj,
    chat_id,
    data,
    cq_id,
    is_owner=False,
    default_project="Главный Штаб",
    bot_token="",
    bot_label="main_hq",
    reply_markup_fn=None,
):
    """Handles interactive Director buttons (dact:deep:, dact:tasks:, smart pdir:exec)."""
    tok = bot_token or ctx["tg_bot_token"]
    pb = (user_obj or {}).get("personal_bot") or {}
    dir_name = pb.get("director_name") or "Тим"
    login = str((user_obj or {}).get("login") or "user").strip().lower().lstrip("@")
    bot_key = "main_hq" if tok == ctx["tg_bot_token"] else (pb.get("bot_username") or login)
    gh_token = ctx.get("gh_token") or ""
    chat_ctx = get_chat_context(bot_key, chat_id, default_project=default_project)
    if reply_markup_fn is None:
        reply_markup_fn = lambda proj_val, extra_row=None: {"inline_keyboard": []}

    if data.startswith("dact:tasks"):
        proj = data.split(":", 2)[2] if data.count(":") >= 2 else (chat_ctx.get("active_project") or default_project)
        ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": "📋 Проверяю свежие задачи..."}, bot_token=tok, bot_label=bot_label)
        ans_md, kb_row = _build_check_tasks_reply(ctx, user_obj, is_owner, {"lang": "ru", "specific_issue": None}, dir_name, proj, gh_token)
        send_tg_smart(ctx, tok, chat_id, ans_md, reply_markup=reply_markup_fn(proj, extra_row=kb_row if kb_row else None), bot_label=bot_label, as_markdown=True)
        return True

    if data.startswith("dact:deep:"):
        iss_s = data.split("dact:deep:", 1)[1].strip()
        try:
            iss_num = int(iss_s)
        except Exception:
            iss_num = None
        if not iss_num:
            return False
        ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"🔬 Готовлю глубокий разбор #{iss_num}..."}, bot_token=tok, bot_label=bot_label)
        # Load issue details safely enforcing scope
        tasks = _fetch_scoped_tasks_with_comments(ctx, user_obj, is_owner=is_owner, limit=5, with_comments_top=1, specific_issue=iss_num)
        if not tasks or all(t.get("number") != iss_num for t in tasks):
            send_tg_smart(ctx, tok, chat_id, f"⛔ Задача #{iss_num} не найдена в вашем контуре доступа.", bot_label=bot_label)
            return True
        t_obj = next(t for t in tasks if t.get("number") == iss_num)
        proj = t_obj["project"] or default_project
        nlu = {
            "intent": "notebooklm_research",
            "lang": detect_lang(t_obj["title"] + " " + t_obj["body"]),
            "project": proj,
            "urls": extract_urls(t_obj["title"] + "\n" + t_obj["body"]),
            "specific_issue": iss_num,
            "use_previous_context": False,
            "use_notebooklm": "notebooklm" in (t_obj["title"] + t_obj["body"]).lower(),
            "needs_background": True,
            "requires_issue": False,
            "understood_goal": f"Глубокий разбор задачи #{iss_num}: {t_obj['title'][:120]}",
        }
        send_tg_smart(
            ctx, tok, chat_id,
            f"🔬 <b>Директор «{_esc(dir_name)}» запустил глубокий разбор Задачи #{iss_num}</b>...\n<i>Ответ придёт сюда через 5–15 сек.</i>",
            bot_label=bot_label,
        )
        th = threading.Thread(
            target=_run_background_execution,
            args=(
                ctx, user_obj, is_owner, nlu,
                f"{t_obj['title']}\n\n{t_obj['body']}",
                chat_ctx, dir_name, proj, bot_key, chat_id, tok, bot_label, iss_num, gh_token, reply_markup_fn,
            ),
            daemon=True,
        )
        th.start()
        return True

    # Smart Upgrade for pdir:exec:<proj> so clicking "Шаг #1" executes the ACTUAL previous request/URL!
    if data.startswith("pdir:exec"):
        parts = data.split(":", 2)
        proj = parts[2] if len(parts) > 2 else (chat_ctx.get("active_project") or default_project)
        last_iss = chat_ctx.get("last_issue_number")
        last_topic = chat_ctx.get("last_topic") or ""
        last_urls = list(chat_ctx.get("last_urls") or [])
        if last_iss or last_topic or last_urls:
            ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"⚡ Выполняю шаг #1 по #{last_iss or 'контексту'}..."}, bot_token=tok, bot_label=bot_label)
            nlu = {
                "intent": "follow_up_step",
                "lang": detect_lang(last_topic),
                "project": proj,
                "urls": last_urls,
                "specific_issue": last_iss,
                "use_previous_context": True,
                "use_notebooklm": "notebooklm" in last_topic.lower(),
                "needs_background": True,
                "requires_issue": False,
                "understood_goal": f"Выполнение шага #1 по вашему предыдущему запросу ({last_topic[:100]})",
            }
            send_tg_smart(
                ctx, tok, chat_id,
                f"⚡ <b>Директор «{_esc(dir_name)}» привязал Шаг #1 к вашей задаче {('#' + str(last_iss)) if last_iss else ''}!</b>\n"
                f"• 🎯 Контекст: <i>{_esc((last_topic or ', '.join(last_urls))[:160])}</i>\n\n"
                f"<i>⏳ Формирую конкретное решение и практические шаги...</i>",
                reply_markup=reply_markup_fn(proj),
                bot_label=bot_label,
            )
            th = threading.Thread(
                target=_run_background_execution,
                args=(
                    ctx, user_obj, is_owner, nlu,
                    f"Выполни конкретный Шаг #1 и дай готовый результат по запросу: {last_topic} {' '.join(last_urls)}".strip(),
                    chat_ctx, dir_name, proj, bot_key, chat_id, tok, bot_label, last_iss, gh_token, reply_markup_fn,
                ),
                daemon=True,
            )
            th.start()
            return True

    return False
