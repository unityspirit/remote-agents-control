# -*- coding: utf-8 -*-
"""
Telegram Multi-Bot Poller Daemon v34.0 (Antigravity 2.31) for Global Spirit Group ★ Virtual Office.
Hardened security gate (@unityspiritpartners approval-only bot access) + 24/7 Cloud sync heartbeat.
1. Main HQ Bot (@unitybusinessbot):
   - Reliable 1-click auth approvals (appr:<login>, rst_ok:<login>, rst_no:<login>) with retries & zero dropped clicks.
   - Allows ALL users EXCEPT @unityspiritpartners to update & change their passwords directly in Telegram (/passwords, /reset, interactive buttons).
   - Explicitly protects @unityspiritpartners from any password modification via Telegram bot.
   - Owner Primary Control over ALL users' personal bots (/bots, bot_pick, bot_appr, bot_pause, bot_ban, bot_asktok, bot_askmsg).
   - Live visibility & audit log of every action performed by every user's AI Director (/logs, cmd:dirlog, director_actions_log.json).
   - Antigravity 2.31 telemetry (/hydrogen, /antigravity) & Saved-Work Audit (/audit).
2. Personal User Bots:
   - Strictly enforces Owner approval (control_status == "active" required for members).
   - Executes interactive AI Director workflows & mirrors every Director action to @unitybusinessbot and director_actions_log.json.
"""
import sys
import html as _html
import urllib.request
import json
import base64
import datetime
import time
import os
import socket
import hashlib
import secrets
import string

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Safe stdio setup: under pythonw.exe (Windows Task Scheduler / Startup), sys.stdout and sys.stderr are None!
def _configure_safe_stdio():
    log_dir = os.path.join(BASE_DIR, "data")
    log_path = os.path.join(log_dir, "tg_poller_service.log")
    if sys.stdout is None or sys.stderr is None or "--log-file" in sys.argv:
        try:
            os.makedirs(log_dir, exist_ok=True)
            if os.path.exists(log_path) and os.path.getsize(log_path) > 5 * 1024 * 1024:
                try:
                    os.replace(log_path, log_path + ".prev")
                except Exception:
                    pass
            lf = open(log_path, "a", encoding="utf-8", buffering=1)
            if sys.stdout is None or "--log-file" in sys.argv:
                sys.stdout = lf
            if sys.stderr is None or "--log-file" in sys.argv:
                sys.stderr = lf
        except Exception:
            pass
    else:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                try:
                    stream.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass

_configure_safe_stdio()

CONFIG_FILE = os.path.join(BASE_DIR, ".tg_daemon_config.json")
OFFSET_FILE = os.path.join(BASE_DIR, ".tg_bot_offset.txt")
USER_BOT_OFFSETS_FILE = os.path.join(BASE_DIR, ".tg_user_bots_offsets.json")
LOCAL_REGISTRY_FILE = os.path.join(BASE_DIR, "auth_registry.json")

def _load_config():
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    return {
        "tg_bot_token": os.environ.get("TG_BOT_TOKEN") or cfg.get("tg_bot_token", ""),
        "owner_tg_chat_id": str(os.environ.get("OWNER_TG_CHAT_ID") or cfg.get("owner_tg_chat_id", "8538272428")),
        "u_token": os.environ.get("U_TOKEN") or cfg.get("u_token", ""),
        "gtp_token": os.environ.get("GTP_TOKEN") or cfg.get("gtp_token", ""),
        "u_repo": cfg.get("u_repo", "unityspirit/remote-agents-control"),
        "gtp_repo": cfg.get("gtp_repo", "GlobalTeamPartners/remote-agents-control"),
        "gemini_api_key": (os.environ.get("GEMINI_API_KEY") or cfg.get("gemini_api_key") or _resolve_fallback_gemini_key() or "").strip(),
        "personal_bot_tokens": cfg.get("personal_bot_tokens", {}),
    }


def _resolve_fallback_gemini_key():
    strike_env = os.path.join(os.path.dirname(BASE_DIR), "Рязань", "Страйк", ".env")
    if os.path.exists(strike_env):
        try:
            with open(strike_env, "r", encoding="utf-8-sig", errors="ignore") as f:
                for ln in f:
                    if "=" in ln and not ln.lstrip().startswith("#"):
                        k, v = ln.split("=", 1)
                        if k.strip() == "GEMINI_API_KEY" and v.strip():
                            return v.strip().strip('"').strip("'")
        except Exception:
            pass
    return ""

_CFG = _load_config()
TG_BOT_TOKEN = _CFG["tg_bot_token"]
OWNER_TG_CHAT_ID = _CFG["owner_tg_chat_id"]
U_TOKEN = _CFG["u_token"]
GTP_TOKEN = _CFG["gtp_token"]
U_REPO = _CFG["u_repo"]
GTP_REPO = _CFG["gtp_repo"]
GEMINI_API_KEY = _CFG["gemini_api_key"]
PERSONAL_BOT_TOKENS = dict(_CFG.get("personal_bot_tokens") or {})

_cached_registry = None
_cached_registry_ts = 0
_last_net_err_print_ts = 0
_chat_states = {}  # chat_id -> {"mode": ..., "login": ..., "ts": ...}

OFFICE_URL = "https://globalteampartners.github.io/remote-agents-control/?v=34.7"
LOCAL_BOT_ACCESS_FILE = os.path.join(BASE_DIR, ".bot_access_registry.json")
_cached_bot_access = None
_cached_bot_access_ts = 0.0
_last_heartbeat_push_ts = 0.0


def _persist_personal_bot_token_local(login_val, bot_token_val):
    nl = norm_login(login_val)
    tok = (bot_token_val or "").strip()
    if not nl:
        return
    prev_tok = PERSONAL_BOT_TOKENS.get(nl, "")
    if tok == prev_tok:
        return
    if tok:
        PERSONAL_BOT_TOKENS[nl] = tok
    else:
        PERSONAL_BOT_TOKENS.pop(nl, None)
    try:
        cfg = {}
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as cf:
                cfg = json.load(cf)
        if cfg.get("personal_bot_tokens") == PERSONAL_BOT_TOKENS:
            return
        cfg["personal_bot_tokens"] = PERSONAL_BOT_TOKENS
        with open(CONFIG_FILE, "w", encoding="utf-8") as cf:
            json.dump(cfg, cf, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _inject_local_bot_tokens(reg):
    if not isinstance(reg, dict):
        return reg
    for u in reg.get("users", []):
        nl = norm_login(u.get("login"))
        local_tok = PERSONAL_BOT_TOKENS.get(nl, "")
        if local_tok:
            pb = u.setdefault("personal_bot", {})
            if not pb.get("bot_token"):
                pb["bot_token"] = local_tok
    return reg


def _strip_bot_tokens_for_public_repo(obj):
    cloned = json.loads(json.dumps(obj, ensure_ascii=False))
    for u in cloned.get("users", []):
        pb = u.get("personal_bot")
        if isinstance(pb, dict) and pb.get("bot_token"):
            _persist_personal_bot_token_local(u.get("login"), pb["bot_token"])
            pb["bot_token"] = ""
    return cloned
PROTECTED_BOT_PWD_ACCOUNTS = {
    "unityspiritpartners",
    "unityspiritpartner",
    "unityspiritteam@gmail.com",
    "unityspiritteam",
    "owner",
    "vlaslav",
}


def norm_login(val):
    return str(val or "").replace("'", "").replace('"', "").strip().lower().lstrip("@")


def is_protected_bot_pwd_account(val):
    nl = norm_login(val)
    return nl in PROTECTED_BOT_PWD_ACCOUNTS


def is_owner_chat(chat_id):
    return str(chat_id) == str(OWNER_TG_CHAT_ID)


def get_persisted_offset():
    try:
        if os.path.exists(OFFSET_FILE):
            with open(OFFSET_FILE, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val.isdigit():
                    return int(val)
    except Exception:
        pass
    return 0


def save_persisted_offset(offset):
    try:
        with open(OFFSET_FILE, "w", encoding="utf-8") as f:
            f.write(str(offset))
    except Exception:
        pass


def get_user_bot_offsets():
    try:
        if os.path.exists(USER_BOT_OFFSETS_FILE):
            with open(USER_BOT_OFFSETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_user_bot_offsets(obj):
    try:
        with open(USER_BOT_OFFSETS_FILE, "w", encoding="utf-8") as f:
            json.dump(obj, f)
    except Exception:
        pass


def log_throttled_err(prefix, err):
    global _last_net_err_print_ts
    now = time.time()
    if now - _last_net_err_print_ts >= 60:
        _last_net_err_print_ts = now
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {prefix}: {err}", flush=True)


import bot_mesh_hub
import director_nlu_engine

_TG_COOLDOWN_UNTIL = {}


def _is_owner_login(val):
    return norm_login(val) in ("unityspiritpartners", "unityspiritpartner", "unityspiritteam@gmail.com")


def _mesh_create_issue(project_name, task_title, body_text):
    num = create_github_issue_task(project_name, task_title, body_text)
    url = f"https://github.com/{GTP_REPO}/issues/{num}" if num else ""
    return bool(num), num, url


def _get_mesh_ctx():
    return {
        "primary_repo": GTP_REPO,
        "gh_token": GTP_TOKEN,
        "gemini_api_key": GEMINI_API_KEY,
        "tg_bot_token": TG_BOT_TOKEN,
        "owner_chat_id": OWNER_TG_CHAT_ID,
        "personal_bot_tokens": PERSONAL_BOT_TOKENS,
        "call_tg": call_tg,
        "norm_login": norm_login,
        "fetch_json": fetch_json_from_github,
        "mutate_repos": mutate_json_across_repos,
        "fetch_registry": fetch_registry,
        "find_user": find_user_in_registry,
        "is_owner_chat": is_owner_chat,
        "is_owner_login": _is_owner_login,
        "create_issue": _mesh_create_issue,
        "create_issue_full": create_github_issue_task,
        "record_action": record_director_action_and_mirror,
        "send_document": call_tg_send_document,
    }


def call_tg_send_document(chat_id, file_path, caption="", bot_token=TG_BOT_TOKEN):
    """Upload a real storefront/CSV deliverable directly into Telegram chat."""
    if not bot_token or not os.path.exists(file_path):
        return None
    boundary = f"----GSGForm{secrets.token_hex(8)}"
    filename = os.path.basename(file_path)
    with open(file_path, "rb") as fh:
        file_bytes = fh.read()
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\n{caption[:900]}\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode("utf-8"),
        file_bytes,
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ]
    body = b"".join(parts)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendDocument",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Connection": "close"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _handle_tg_media_message(msg, bot_token, sender_login, proj, bot_label="main_hq"):
    """Download incoming Telegram photo/document to Входящие_Офис and index in MEDIA_INDEX + crm_data.json."""
    if not isinstance(msg, dict):
        return "", ""
    file_id = ""
    raw_name = ""
    kind_ru = ""
    if msg.get("photo") and isinstance(msg["photo"], list):
        best = max(msg["photo"], key=lambda p: (int(p.get("file_size") or 0), int(p.get("width") or 0)))
        file_id = str(best.get("file_id") or "")
        raw_name = "photo.jpg"
        kind_ru = "Фото"
    elif isinstance(msg.get("document"), dict):
        doc = msg["document"]
        file_id = str(doc.get("file_id") or "")
        raw_name = str(doc.get("file_name") or "document.bin")
        kind_ru = f"Файл ({raw_name})"
    if not file_id or not bot_token:
        return "", ""

    try:
        gf = call_tg("getFile", {"file_id": file_id}, bot_token=bot_token, retries=2, bot_label=bot_label)
        f_path = str(((gf or {}).get("result") or {}).get("file_path") or "").strip()
        if not f_path:
            return "", ""
        ext = os.path.splitext(raw_name)[1] or os.path.splitext(f_path)[1] or ".bin"
        base_stem = re.sub(r"[^A-Za-z0-9_.\-]+", "_", os.path.splitext(raw_name)[0])[:48] or "tg_media"
        msg_id = int(msg.get("message_id") or int(time.time()))
        safe_fname = f"tg_{msg_id}_{base_stem}{ext}"
        inbox_root = r"C:\Users\Vlaslav\Downloads\Входящие_Офис"
        media_dir = os.path.join(inbox_root, "01_Необработанное", "media")
        os.makedirs(media_dir, exist_ok=True)
        dest_path = os.path.join(media_dir, safe_fname)

        dl_url = f"https://api.telegram.org/file/bot{bot_token}/{f_path}"
        req = urllib.request.Request(dl_url, headers={"User-Agent": "GSG-Bot-Daemon/34.7"})
        with urllib.request.urlopen(req, timeout=22) as resp:
            data_bytes = resp.read(20 * 1024 * 1024)
        with open(dest_path, "wb") as out_f:
            out_f.write(data_bytes)

        rel_path = f"01_Необработанное/media/{safe_fname}"
        caption = str(msg.get("caption") or "").strip()
        idx_path = os.path.join(media_dir, "MEDIA_INDEX.md")
        idx_line = (
            f"- `{safe_fname}` → **Telegram ({proj}, @{sender_login or 'unknown'})**: "
            + (caption[:180] if caption else f"{kind_ru} сохранено без подписи ({len(data_bytes)} байт)")
            + "\n"
        )
        with open(idx_path, "a", encoding="utf-8") as mf:
            mf.write(idx_line)

        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        note_dir = os.path.join(inbox_root, "04_База_Знаний_Синтез")
        os.makedirs(note_dir, exist_ok=True)
        with open(os.path.join(note_dir, f"Note_TG_Media_{msg_id}_{stamp}.md"), "w", encoding="utf-8") as nf:
            nf.write(
                f"# Вложение из Telegram ({proj} · @{sender_login or 'user'})\n\n"
                f"- **Файл:** `{rel_path}` ({len(data_bytes)} байт)\n"
                f"- **Подпись:** {caption or '(без подписи)'}\n"
            )
        return rel_path, kind_ru
    except Exception as exc:
        log_throttled_err("TG media download", exc)
        return "", ""


def call_tg(method, data=None, bot_token=TG_BOT_TOKEN, retries=2, bot_label=""):
    if method == "getUpdates":
        cool_until = _TG_COOLDOWN_UNTIL.get(bot_token, 0.0)
        if cool_until and time.time() < cool_until:
            return None
    label = bot_label or ("main_hq" if bot_token == TG_BOT_TOKEN else f"bot:{str(bot_token)[:8]}")
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    payload = json.dumps(data or {}).encode("utf-8")
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Connection": "close",
                "User-Agent": "GSG-Bot-Daemon/34.2"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=14) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as he:
            err_body = ""
            try:
                err_body = he.read().decode("utf-8", "replace")
            except Exception:
                pass
            retry_after = 0
            if he.code == 429:
                try:
                    retry_after = int(json.loads(err_body).get("parameters", {}).get("retry_after", 8))
                except Exception:
                    retry_after = 10
                _TG_COOLDOWN_UNTIL[bot_token] = time.time() + max(retry_after, 4)
            elif he.code == 409:
                # Another client briefly polled getUpdates; back off briefly without busy-looping
                _TG_COOLDOWN_UNTIL[bot_token] = time.time() + 12
                log_throttled_err(f"TG {method} [{label}]", f"HTTP 409 Conflict (backed off 12s): {err_body[:160]}")
                return None
            if attempt + 1 < retries and he.code not in (401, 403, 404, 409, 429):
                time.sleep(1.0)
            else:
                log_throttled_err(f"TG {method} [{label}]", f"HTTP {he.code}: {err_body[:160] or he}")
                return None
        except Exception as e:
            if attempt + 1 < retries:
                time.sleep(1.0)
            else:
                log_throttled_err(f"TG {method} [{label}]", e)
                return None


def fetch_json_from_github(repo, token, path, branch="data"):
    url = f"https://api.github.com/repos/{repo}/contents/{path}?ref={branch}&t={int(time.time())}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GSG-Bot-Daemon/29.0"
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        content = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
        return content, data.get("sha")


def fetch_registry(force=False):
    global _cached_registry, _cached_registry_ts
    now = time.time()
    if not force and _cached_registry and (now - _cached_registry_ts < 8):
        return _cached_registry
    for repo, tok in [(GTP_REPO, GTP_TOKEN), (U_REPO, U_TOKEN)]:
        for br in ["data", "main"]:
            try:
                reg, _ = fetch_json_from_github(repo, tok, "auth_registry.json", br)
                _inject_local_bot_tokens(reg)
                _cached_registry = reg
                _cached_registry_ts = now
                try:
                    with open(LOCAL_REGISTRY_FILE, "w", encoding="utf-8") as lf:
                        json.dump(_strip_bot_tokens_for_public_repo(reg), lf, indent=2, ensure_ascii=False)
                except Exception:
                    pass
                return reg
            except Exception as e:
                log_throttled_err(f"Fetch registry {repo}[{br}]", e)
    if os.path.exists(LOCAL_REGISTRY_FILE):
        try:
            with open(LOCAL_REGISTRY_FILE, "r", encoding="utf-8") as lf:
                return _inject_local_bot_tokens(json.load(lf))
        except Exception:
            pass
    return _inject_local_bot_tokens(_cached_registry or {"users": []})


def mutate_json_across_repos(file_path, mutator_fn, commit_msg, branches=None, default_factory=None):
    if branches is None:
        branches = ["data", "main"]
    updated_any = False
    latest_obj = None

    for repo, token in [(GTP_REPO, GTP_TOKEN), (U_REPO, U_TOKEN)]:
        for branch in branches:
            for attempt in range(3):
                try:
                    sha = None
                    try:
                        obj, sha = fetch_json_from_github(repo, token, file_path, branch)
                    except urllib.error.HTTPError as he:
                        if he.code == 404 and default_factory:
                            obj = default_factory()
                        else:
                            raise
                    if file_path == "auth_registry.json":
                        _inject_local_bot_tokens(obj)
                    changed = mutator_fn(obj)
                    if not changed:
                        break
                    obj_to_push = _strip_bot_tokens_for_public_repo(obj) if file_path == "auth_registry.json" else obj
                    put_content = json.dumps(obj_to_push, indent=2, ensure_ascii=False)
                    put_b64 = base64.b64encode(put_content.encode("utf-8")).decode("ascii")
                    payload = {
                        "message": commit_msg,
                        "content": put_b64,
                        "branch": branch
                    }
                    if sha:
                        payload["sha"] = sha
                    put_url = f"https://api.github.com/repos/{repo}/contents/{file_path}"
                    req_put = urllib.request.Request(
                        put_url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={
                            "Authorization": f"token {token}",
                            "Accept": "application/vnd.github.v3+json",
                            "Content-Type": "application/json",
                            "User-Agent": "GSG-Bot-Daemon/29.0"
                        },
                        method="PUT"
                    )
                    with urllib.request.urlopen(req_put, timeout=20) as p_resp:
                        json.loads(p_resp.read().decode("utf-8"))
                        updated_any = True
                        latest_obj = obj
                        break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(1.2)
                    else:
                        print(f"Warn mutating {file_path} {repo}[{branch}]: {e}", flush=True)
    return updated_any, latest_obj


def mutate_registry_across_repos(mutator_fn, commit_msg):
    global _cached_registry, _cached_registry_ts
    ok, reg = mutate_json_across_repos("auth_registry.json", mutator_fn, commit_msg, branches=["data", "main"])
    if ok and reg:
        _inject_local_bot_tokens(reg)
        _cached_registry = reg
        _cached_registry_ts = time.time()
        try:
            with open(LOCAL_REGISTRY_FILE, "w", encoding="utf-8") as lf:
                json.dump(_strip_bot_tokens_for_public_repo(reg), lf, indent=2, ensure_ascii=False)
        except Exception:
            pass
    return ok


def find_user_in_registry(reg, target_login):
    t = norm_login(target_login)
    aliases = {
        "yura": "yvyatkin",
        "юра": "yvyatkin",
        "jv": "yvyatkin",
        "alberta": "aliashirani",
        "tatiana": "tatianaknyazeva777",
        "татьяна": "tatianaknyazeva777",
        "owner": "unityspiritpartners",
        "vlaslav": "unityspiritpartners",
    }
    t_alias = aliases.get(t, t)
    users = reg.get("users", [])
    for u in users:
        u_l = norm_login(u.get("login"))
        if u_l == t or u_l == t_alias:
            return u
    for u in users:
        u_tg = norm_login(u.get("telegram"))
        if u_tg == t or u_tg == t_alias:
            return u
    return None


def find_users_by_tg_sender(reg, from_user, chat_id):
    uname = norm_login((from_user or {}).get("username"))
    c_str = str(chat_id or "")
    matches = []
    seen = set()
    aliases = {
        "yura": {"yvyatkin", "jv"},
        "yvyatkin": {"yvyatkin", "jv"},
        "alberta": {"aliashirani", "alberta_shirani"},
        "tatiana": {"tatianaknyazeva777"},
    }
    alias_set = aliases.get(uname, set())
    for u in reg.get("users", []):
        u_l = norm_login(u.get("login"))
        if is_protected_bot_pwd_account(u_l):
            continue
        u_tg = norm_login(u.get("telegram"))
        matched = False
        if uname and (u_l == uname or u_tg == uname or u_l in alias_set):
            matched = True
        elif c_str and str(u.get("verified_bot_chat_id") or "") == c_str:
            matched = True
        if matched and u_l not in seen:
            seen.add(u_l)
            matches.append(u)
    return matches


def approve_user_in_github(login_to_approve):
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    now_ts = int(time.time())

    def _mutate(reg):
        u = find_user_in_registry(reg, login_to_approve)
        if not u:
            return False
        u_norm = norm_login(u.get("login"))
        u["status"] = "approved"
        u["approved_at"] = u.get("approved_at") or now_iso
        u["owner_action_stamp"] = f"tg-bot-appr-{now_ts}"
        if not u.get("assigned_projects"):
            u["assigned_projects"] = [f"Личный Офис: @{u_norm}"]
        return True

    return mutate_registry_across_repos(_mutate, f"approve: user {norm_login(login_to_approve)} via @unitybusinessbot [v34.0]")


def set_user_password_in_github(login_target, new_password, verified_chat_id=None):
    if is_protected_bot_pwd_account(login_target):
        return False
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    now_ts = int(time.time())
    salt_bytes = os.urandom(16)
    salt_hex = salt_bytes.hex()
    dk = hashlib.pbkdf2_hmac("sha256", new_password.encode("utf-8"), salt_bytes, 120000).hex()

    def _mutate(reg):
        u = find_user_in_registry(reg, login_target)
        if not u or is_protected_bot_pwd_account(u.get("login")):
            return False
        u["salt_hex"] = salt_hex
        u["pbkdf2_hash"] = dk
        u["iterations"] = 120000
        u["status"] = "approved"
        u["approved_at"] = u.get("approved_at") or now_iso
        u["pending_reset"] = None
        u["owner_action_stamp"] = f"tg-bot-pwd-{now_ts}"
        if verified_chat_id and not is_owner_chat(verified_chat_id):
            u["verified_bot_chat_id"] = str(verified_chat_id)
        return True

    return mutate_registry_across_repos(_mutate, f"pwd-update: {norm_login(login_target)} via @unitybusinessbot [v34.0]")


def save_pending_bot_reset_in_github(login_target, new_password, req_chat_id, req_tg_username=""):
    if is_protected_bot_pwd_account(login_target):
        return False, None
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    salt_bytes = os.urandom(16)
    salt_hex = salt_bytes.hex()
    dk = hashlib.pbkdf2_hmac("sha256", new_password.encode("utf-8"), salt_bytes, 120000).hex()
    code = f"TG-{secrets.randbelow(900000) + 100000}"

    def _mutate(reg):
        u = find_user_in_registry(reg, login_target)
        if not u or is_protected_bot_pwd_account(u.get("login")):
            return False
        u["pending_reset"] = {
            "code": code,
            "requested_at": now_iso,
            "salt_hex": salt_hex,
            "pbkdf2_hash": dk,
            "iterations": 120000,
            "requested_chat_id": str(req_chat_id),
            "requested_tg": req_tg_username or ""
        }
        return True

    ok = mutate_registry_across_repos(_mutate, f"bot-reset-req: {norm_login(login_target)} via @unitybusinessbot [v34.0]")
    return ok, code


def handle_password_reset_in_github(login_target, approve_bool, explicit_new_password=None):
    if is_protected_bot_pwd_account(login_target):
        return False
    if explicit_new_password:
        return set_user_password_in_github(login_target, explicit_new_password)

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    now_ts = int(time.time())

    def _mutate(reg):
        u = find_user_in_registry(reg, login_target)
        if not u or is_protected_bot_pwd_account(u.get("login")):
            return False
        pr = u.get("pending_reset")
        if pr and approve_bool:
            if pr.get("salt_hex") and pr.get("pbkdf2_hash"):
                u["salt_hex"] = pr["salt_hex"]
                u["pbkdf2_hash"] = pr["pbkdf2_hash"]
                u["iterations"] = pr.get("iterations", 120000)
            if pr.get("rsa_envelope"):
                u["rsa_envelope"] = pr["rsa_envelope"]
            if pr.get("requested_chat_id") and not is_owner_chat(pr.get("requested_chat_id")):
                u["verified_bot_chat_id"] = str(pr["requested_chat_id"])
            u["status"] = "approved"
            u["approved_at"] = u.get("approved_at") or now_iso
            u["pending_reset"] = None
            u["owner_action_stamp"] = f"rst-ok-{now_ts}"
            return True
        elif pr and not approve_bool:
            u["pending_reset"] = None
            u["owner_action_stamp"] = f"rst-rej-{now_ts}"
            return True
        elif approve_bool:
            u["status"] = "approved"
            u["owner_action_stamp"] = f"rst-ok-{now_ts}"
            return True
        return False

    act = "approve-reset" if approve_bool else "reject-reset"
    return mutate_registry_across_repos(_mutate, f"{act}: {norm_login(login_target)} via @unitybusinessbot [v34.0]")


_PAIRED_ACCOUNT_GROUPS = (
    ("aliashirani", "alberta_shirani"),
    ("yvyatkin", "jv"),
)


def _paired_logins_for(login_val):
    nl = norm_login(login_val)
    for grp in _PAIRED_ACCOUNT_GROUPS:
        if nl in grp:
            return list(grp)
    return [nl] if nl else []


def update_user_bot_control(login_target, action, bot_token=None, director_name=None):
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    now_ts = int(time.time())
    bot_username = ""
    if bot_token and ":" in bot_token:
        me = call_tg("getMe", bot_token=bot_token)
        if me and me.get("ok"):
            bot_username = me["result"].get("username", "")
    if action == "set_token" and not bot_username:
        return False, "invalid_token"

    pair_list = _paired_logins_for(login_target)

    def _mutate(reg):
        targets = []
        for candidate in pair_list:
            u_hit = find_user_in_registry(reg, candidate)
            if u_hit and all(x is not u_hit for x in targets):
                targets.append(u_hit)
        if not targets:
            u0 = find_user_in_registry(reg, login_target)
            if u0:
                targets.append(u0)
        if not targets:
            return False

        for u in targets:
            pb = u.setdefault("personal_bot", {
                "director_name": "Тим",
                "bot_token": "",
                "bot_username": "",
                "chat_id": "",
                "auto_send_reports": True,
                "control_status": "not_configured"
            })
            if action == "approve":
                pb["control_status"] = "active"
                pb["approved_by_owner_at"] = now_iso
            elif action == "pause":
                pb["control_status"] = "paused"
            elif action == "disable":
                pb["control_status"] = "disabled"
                pb["bot_token"] = ""
                pb["bot_username"] = ""
            elif action == "set_token":
                pb["bot_token"] = (bot_token or "").strip()
                pb["bot_username"] = "@" + bot_username
                pb["control_status"] = "active"
                pb["approved_by_owner_at"] = now_iso
            elif action == "set_director" and director_name:
                pb["director_name"] = director_name.strip()
            pb["updated_at"] = now_iso
            u["owner_action_stamp"] = f"bot-{action}-{now_ts}"
        return True

    ok = mutate_registry_across_repos(_mutate, f"bot-ctrl({action}): {norm_login(login_target)} via @unitybusinessbot [v34.3]")
    if ok and action == "set_token":
        for candidate in pair_list or [login_target]:
            _persist_personal_bot_token_local(candidate, bot_token)
    elif ok and action == "disable":
        for candidate in pair_list or [login_target]:
            _persist_personal_bot_token_local(candidate, "")
    return ok, bot_username


def create_github_issue_task(project_name, task_title, body_text):
    url = f"https://api.github.com/repos/{GTP_REPO}/issues"
    req = urllib.request.Request(
        url,
        data=json.dumps({
            "title": f"CMD: {task_title} | PROJ: {project_name}",
            "body": body_text
        }).encode("utf-8"),
        headers={
            "Authorization": f"token {GTP_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "GSG-Bot-Daemon/29.0"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            res = json.loads(r.read().decode("utf-8"))
            return res.get("number")
    except Exception:
        return None


def record_director_action_and_mirror(user_obj, action_type, project_name, summary_text, source="telegram_bot", mirror_to_owner=True):
    login = norm_login(user_obj.get("login") or "user")
    full_name = user_obj.get("full_name") or login
    pb = user_obj.get("personal_bot") or {}
    dir_name = pb.get("director_name") or "Тим"
    bot_uname = pb.get("bot_username") or "Личный бот"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    now_local = datetime.datetime.now().strftime("%H:%M:%S")

    entry = {
        "id": f"dirlog-{int(time.time()*1000)}",
        "ts": now_iso,
        "time_local": now_local,
        "user_login": login,
        "full_name": full_name,
        "bot_username": bot_uname,
        "director_name": dir_name,
        "action_type": action_type,
        "project": project_name or "Личный Офис",
        "summary": str(summary_text or "")[:600],
        "source": source
    }

    def _mutate_log(log_obj):
        items = log_obj.setdefault("actions", [])
        items.insert(0, entry)
        log_obj["actions"] = items[:150]
        log_obj["updated_at"] = now_iso
        return True

    mutate_json_across_repos(
        "director_actions_log.json",
        _mutate_log,
        f"audit(director): {login} ({dir_name}) -> {action_type}",
        branches=["data"],
        default_factory=lambda: {"version": "34.0", "actions": []}
    )

    if mirror_to_owner and login not in ("unityspiritpartners", "unityspiritpartner", "unityspiritteam@gmail.com"):
        mirror_msg = (
            f"👁️ <b>КОНТРОЛЬ ДИРЕКТОРА СОТРУДНИКА</b>\n\n"
            f"• <b>Пользователь:</b> <code>@{_esc_h(login)}</code> ({_esc_h(full_name)})\n"
            f"• <b>Бот / Директор:</b> {_esc_h(bot_uname)} · «<b>{_esc_h(dir_name)}</b>»\n"
            f"• <b>Папка:</b> «{_esc_h(project_name)}»\n"
            f"• <b>Действие ({_esc_h(action_type)}):</b>\n<i>{_esc_h(str(summary_text)[:450])}</i>\n"
            f"• <b>Время:</b> {_esc_h(now_local)}"
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": f"⏸️ Пауза бота (@{login})", "callback_data": f"bot_pause:{login}"},
                    {"text": f"💬 Написать в бот @{login}", "callback_data": f"bot_askmsg:{login}"}
                ],
                [
                    {"text": "📜 Весь журнал Директоров", "callback_data": "cmd:dirlog"},
                    {"text": "🏢 Контроль в Офисе", "url": OFFICE_URL}
                ]
            ]
        }
        call_tg("sendMessage", {
            "chat_id": OWNER_TG_CHAT_ID,
            "text": mirror_msg,
            "parse_mode": "HTML",
            "reply_markup": kb
        })


def build_director_inline_keyboard(project_name):
    return bot_mesh_hub.build_mesh_director_keyboard(project_name)


def generate_director_reply(user_obj, action_type, project_name, custom_text=""):
    login = norm_login(user_obj.get("login") or "user")
    pb = user_obj.get("personal_bot") or {}
    dir_name = pb.get("director_name") or "Тим"
    full_name = user_obj.get("full_name") or login

    if "alberta" in login or "aliashirani" in login:
        proj = "Alberta"
        ctx_status = (
            "• 🔥 <b>Сегодняшние задачи (12 ноя):</b> обработаны 4 запроса (#47–#50): триггер женщин 30–50 + мини-продукт, пивот клуба, бот 1M ₽, быстрые клиенты.\n"
            "• 🎯 <b>Главные:</b> Закрытый клуб «Трансформация женщин 30–50 лет» (опора, уверенность, стиль, доход, отношения, снятие тревожности, свои желания) + VIP 99k–150k ₽.\n"
            "• 🪜 <b>Промежуточные:</b> Хабы <i>alberta-hub</i> и <i>Международный Штаб 5-в-1</i> подключены прямо в Офисе.\n"
            "• ⚡ <b>Параллельные:</b> AI-бота @lermontovplusbot и 900 WebP кадров Cinema WebApp."
        )
        ctx_next = (
            "🔥 <b>Сегодня:</b> Утвердить тему главного триггера женщин 30–50 и оффер анти-возраженческого мини-продукта (#50).\n"
            "🎯 <b>Главное:</b> Запустить Telegram-воронку подписки клуба на 1 000 000 ₽ (#48–#49).\n"
            "🪜 <b>Промежуточное:</b> Прикрепить 30-сек видео и ключи ЮKassa/Prodamus.\n"
            "⚡ <b>Параллельно:</b> Рендер 7 Reels VEO 3.1 + быстрые клиенты (#47)."
        )
        ctx_ideas = (
            "💎 <b>Идея 1 (Быстрый трипвайер #50):</b> Мини-продукт «Возврат Опоры за 72 часа: как отделить свои желания от чужих и убрать фоновую тревогу» (990–1990 ₽, 0 возражений).\n"
            "💎 <b>Идея 2 (Клуб #49):</b> Закрытый клуб для женщин 30–50 лет (опора, речь, стиль, отношения, баланс и доход) — 4 900 / 14 900 ₽ в мес.\n"
            "💎 <b>Идея 3 (Быстрые клиенты #47):</b> Сессия-диагностика «Точка Сборки 30–50» для тёплых лидов с переходом в личное сопровождение."
        )
    elif "yvyatkin" in login or login == "jv":
        proj = "Юра"
        ctx_status = (
            "• <b>KHL AI Predictor (модель на паузе на ПК):</b> калибровочные метрики модели — WinRate 82.9%, ROI +53.4%, Brier 0.1716.\n"
            "• <b>AI Crypto Trading Bot:</b> мониторинг импульсов BTC/ETH/SOL активен 24/7.\n"
            "• <b>VIP-канал прогнозов:</b> отбор кэфов >= 1.85."
        )
        ctx_next = (
            "1️⃣ Опубликовать утренний отчёт с верифицированной статистикой 82.9%.\n"
            "2️⃣ Открыть доступ в VIP-канал (3 900 ₽/мес).\n"
            "3️⃣ Синхронизировать алерты UptrendDipVol."
        )
        ctx_ideas = (
            "💎 <b>Идея 1:</b> Подписка «Победная Серия» — оплата только за плюсовые недели.\n"
            "💎 <b>Идея 2:</b> Авто-калькулятор ставки по критерию Келли в боте.\n"
            "💎 <b>Идея 3:</b> Кросс-пакет КХЛ + Крипто."
        )
    elif "tatiana" in login:
        proj = "Татьяна tentorium.ru"
        ctx_status = (
            "• <b>Каталог tentorium.ru:</b> ключевые велнес-наборы структурированы.\n"
            "• <b>AI-Консультант 24/7:</b> скрипты подбора продукции активны.\n"
            "• <b>Лояльность:</b> цепочка повторных касаний подготовлена."
        )
        ctx_next = (
            "1️⃣ Выпустить подборку «Топ-5 наборов для иммунитета и энергии».\n"
            "2️⃣ Запустить ИИ-викторину (3 вопроса о здоровье).\n"
            "3️⃣ Отправить реактивацию постоянным клиентам с подарком."
        )
        ctx_ideas = (
            "💎 <b>Идея 1:</b> Подписка «Велнес-Бокс месяца» со скидкой 15%.\n"
            "💎 <b>Идея 2:</b> ИИ-нутрициолог в Telegram по составу продуктов.\n"
            "💎 <b>Идея 3:</b> Реферальный бонус за фотоотзыв."
        )
    else:
        proj = project_name or "Главный Штаб"
        proj_h_tmp = _esc_h(proj)
        ctx_status = (
            f"• <b>Активная папка:</b> «{proj_h_tmp}»\n"
            "• <b>Автономные агенты 24/7:</b> все отделы и 32 проекта синхронизированы.\n"
            "• <b>Топ MVP #1:</b> AltaDesk AI Voice ($750 setup + $350/мес, маржа 94.8%)."
        )
        ctx_next = (
            "1️⃣ Отправить КП AltaDesk AI Voice первым 20 отелям и клиникам.\n"
            f"2️⃣ Проверить свежие отчёты агентов в папке «{proj_h_tmp}».\n"
            "3️⃣ Запустить ресёрч новой ниши через NotebookLM RAG."
        )
        ctx_ideas = (
            "💎 <b>Идея 1:</b> 7-дневный тест ИИ-голосового администратора с переходом на $350/мес.\n"
            "💎 <b>Идея 2:</b> Пакет «3D Кино-Сайт + ИИ-Бот» ($1,500).\n"
            "💎 <b>Идея 3:</b> Авто-поиск бизнесов без виджета связи."
        )

    dir_h = _esc_h(dir_name)
    proj_h = _esc_h(proj)
    full_h = _esc_h(full_name)
    custom_h = _esc_h(custom_text)
    summary_for_log = f"Команда {action_type} в папке «{proj}»"
    if action_type == "status":
        reply = (
            f"👑 <b>Личный Директор «{dir_h}» — Сводка по папке «{proj_h}»</b>\n\n"
            f"Здравствуйте, <b>{full_h}</b>! Текущее состояние работ:\n\n"
            f"{ctx_status}\n\n"
            f"💬 <b>Вопрос от «{dir_h}»:</b> <i>Что делаем дальше?</i>"
        )
        summary_for_log = f"Выдана сводка по папке «{proj}»"
    elif action_type == "next":
        reply = (
            f"🚀 <b>Личный Директор «{dir_h}» — План «Что делаем дальше?»</b>\n\n"
            f"Для папки <b>«{proj_h}»</b> рекомендую 3 приоритетных шага:\n\n"
            f"{ctx_next}\n\n"
            f"💬 <b>Вопрос от «{dir_h}»:</b> <i>Нажмите «⚡ Выполнить шаг #1» или напишите своё задание:</i>"
        )
        summary_for_log = f"Сформирован план из 3 шагов для «{proj}»"
    elif action_type == "exec":
        iss_num = create_github_issue_task(proj, f"DIRECTOR_{dir_name.upper()}: Выполнение шага #1 ({proj})", f"Запущено @{login} через личного директора «{dir_name}».")
        num_str = f" (Задача #{iss_num})" if iss_num else ""
        reply = (
            f"⚡ <b>Личный Директор «{dir_h}» — Задача принята{num_str}!</b>\n\n"
            f"• Папка: <b>«{proj_h}»</b>\n"
            f"• Статус: <b>🟢 ИИ-агенты выполняют приоритетный шаг #1</b>\n\n"
            f"💬 <b>Вопрос от «{dir_h}»:</b> <i>Подготовить 3 идеи роста дохода или провести ресёрч?</i>"
        )
        summary_for_log = f"Запущен шаг #1 в папке «{proj}»{num_str}"
    elif action_type == "ideas":
        reply = (
            f"💡 <b>Личный Директор «{dir_h}» — 3 Идеи для дохода («{proj_h}»)</b>\n\n"
            f"{ctx_ideas}\n\n"
            f"💬 <b>Вопрос от «{dir_h}»:</b> <i>Какую идею запускаем? Напишите номер 1, 2 или 3:</i>"
        )
        summary_for_log = f"Предложены 3 идеи монетизации для «{proj}»"
    elif action_type == "research":
        reply = (
            f"🔎 <b>Личный Директор «{dir_h}» — Режим Исследования</b>\n\n"
            f"Пришлите любую <b>тему, вопрос или ссылку</b> — агенты разберут материал и сохранят в папку <b>«{proj_h}»</b>."
        )
        summary_for_log = f"Открыт режим исследования в «{proj}»"
    elif action_type == "approve":
        reply = (
            f"✅ <b>Личный Директор «{dir_h}» — Этап принят!</b>\n\n"
            f"Отлично, <b>{full_h}</b>! Работа в папке <b>«{proj_h}»</b> зафиксирована. Переходим к следующей задаче."
        )
        summary_for_log = f"Пользователь принял выполненную работу по «{proj}»"
    else:
        iss_num = create_github_issue_task(proj, f"TIM: [{dir_name}] {custom_text[:120]}", f"Отправлено @{login} через бота (Директор: {dir_name}):\n\n{custom_text}")
        num_str = f" (Задача #{iss_num})" if iss_num else ""
        reply = (
            f"👑 <b>Личный Директор «{dir_h}» принял ваше поручение{num_str}!</b>\n\n"
            f"📥 <b>Текст:</b> <i>«{custom_h}»</i>\n"
            f"📁 <b>Папка:</b> «{proj_h}»\n\n"
            f"💬 <b>Вопрос от «{dir_h}»:</b> <i>Что делаем дальше параллельно?</i>"
        )
        summary_for_log = f"Поручение от @{login}{num_str}: {custom_text[:200]}"

    record_director_action_and_mirror(user_obj, action_type, proj, summary_for_log)
    return reply


def poll_personal_user_bots():
    reg = fetch_registry(force=False)
    offsets = get_user_bot_offsets()
    offsets_changed = False
    polled_tokens = set()

    for u in reg.get("users", []):
        pb = u.get("personal_bot")
        if not isinstance(pb, dict):
            continue
        u_login = norm_login(u.get("login"))
        b_tok = (pb.get("bot_token") or PERSONAL_BOT_TOKENS.get(u_login) or "").strip()
        if not b_tok or ":" not in b_tok or b_tok == TG_BOT_TOKEN or b_tok in polled_tokens:
            continue
        polled_tokens.add(b_tok)
        bot_uname = str(pb.get("bot_username") or f"@{u_login}_bot").strip()
        ctrl_status = pb.get("control_status") or ("active" if u.get("role") == "owner" else "pending_owner")
        cur_off = int(offsets.get(b_tok, 0))
        updates = call_tg("getUpdates", {"offset": cur_off, "timeout": 1}, bot_token=b_tok, retries=1, bot_label=bot_uname)
        if not updates or not updates.get("ok"):
            continue

        for item in updates.get("result", []):
            cur_off = max(cur_off, item["update_id"] + 1)
            offsets[b_tok] = cur_off
            offsets_changed = True

            assigned = [p for p in (u.get("assigned_projects") or []) if p != "*"]
            default_proj = assigned[0] if assigned else f"Личный Офис: @{u_login}"
            dir_name = pb.get("director_name") or "Тим"

            # Determine chat_id
            if "callback_query" in item:
                cq = item["callback_query"]
                c_id = str(cq.get("message", {}).get("chat", {}).get("id") or cq.get("from", {}).get("id") or "")
            elif "message" in item:
                c_id = str(item["message"]["chat"]["id"])
            else:
                continue

            if c_id and str(pb.get("chat_id") or "") != c_id:
                pb["chat_id"] = c_id
                pair_set = set(_paired_logins_for(u_login))
                def _link_cid(r):
                    did = False
                    for x in r.get("users", []):
                        if norm_login(x.get("login")) in pair_set:
                            x.setdefault("personal_bot", {})["chat_id"] = c_id
                            if not is_owner_chat(c_id):
                                x["verified_bot_chat_id"] = c_id
                            did = True
                    return did
                mutate_registry_across_repos(_link_cid, f"feat(bot): auto-link chat_id {c_id} for {u_login}")

            # Enforce Owner Primary Control!
            if u.get("role") != "owner" and ctrl_status != "active":
                if "callback_query" in item:
                    call_tg("answerCallbackQuery", {
                        "callback_query_id": item["callback_query"]["id"],
                        "text": "⏳ Бот ожидает одобрения или поставлен на паузу Главным Штабом.",
                        "show_alert": True
                    }, bot_token=b_tok)
                else:
                    msg_st = (
                        f"⏸️ <b>Управление этим ботом сейчас на паузе по решению Главного Штаба.</b>"
                        if ctrl_status in ("paused", "disabled")
                        else f"⏳ <b>Ваш личный бот и Директор «{_esc_h(dir_name)}» ожидают подтверждения Главного Штаба (@unityspiritpartners).</b>\n\nЗаявка на активацию уже отправлена Владельцу."
                    )
                    call_tg("sendMessage", {"chat_id": c_id, "text": msg_st, "parse_mode": "HTML"}, bot_token=b_tok)
                    if ctrl_status == "pending_owner":
                        call_tg("sendMessage", {
                            "chat_id": OWNER_TG_CHAT_ID,
                            "text": f"🤖 <b>ПОЛЬЗОВАТЕЛЬ АКТИВИРОВАЛ БОТА (/start) — ЖДЁТ ВАШЕГО КОНТРОЛЯ</b>\n\n• Логин: <code>@{_esc_h(u_login)}</code>\n• Директор: <b>«{_esc_h(dir_name)}»</b>\n• Бот: {_esc_h(pb.get('bot_username') or 'Telegram Bot')}",
                            "parse_mode": "HTML",
                            "reply_markup": {"inline_keyboard": [
                                [{"text": f"✅ Одобрить и включить бот @{u_login}", "callback_data": f"bot_appr:{u_login}"}],
                                [{"text": "⏸️ Оставить на паузе", "callback_data": f"bot_pause:{u_login}"}]
                            ]}
                        })
                continue

            if "callback_query" in item:
                cq = item["callback_query"]
                cq_id = cq["id"]
                data = cq.get("data", "")
                if bot_mesh_hub.handle_personal_bot_mesh_interaction(_get_mesh_ctx(), item, u, b_tok, bot_uname, reg):
                    continue

                if data == "pdir:rename":
                    call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "Выберите имя Директора"}, bot_token=b_tok)
                    name_kb = {
                        "inline_keyboard": [
                            [{"text": "👑 Тим (Tim)", "callback_data": "pdir:setname:Тим"}, {"text": "🧠 Джарвис (Jarvis)", "callback_data": "pdir:setname:Джарвис"}],
                            [{"text": "⚡ Атлас (Atlas)", "callback_data": "pdir:setname:Атлас"}, {"text": "🌸 Аврора (Aurora)", "callback_data": "pdir:setname:Аврора"}],
                            [{"text": "🚀 Нео (Neo)", "callback_data": "pdir:setname:Нео"}, {"text": "💎 Виктория (Victoria)", "callback_data": "pdir:setname:Виктория"}]
                        ]
                    }
                    call_tg("sendMessage", {
                        "chat_id": c_id,
                        "text": "🏷️ <b>Выберите имя Личного AI-Директора</b> или напишите: <code>/name ВашеИмя</code>",
                        "parse_mode": "HTML",
                        "reply_markup": name_kb
                    }, bot_token=b_tok)
                elif data.startswith("pdir:setname:"):
                    new_name = data.split("pdir:setname:", 1)[1].strip()
                    update_user_bot_control(u_login, "set_director", director_name=new_name)
                    pb["director_name"] = new_name
                    record_director_action_and_mirror(u, "rename", default_proj, f"Смена имени Директора на «{new_name}»")
                    call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"✅ Директора теперь зовут «{new_name}»!"}, bot_token=b_tok)
                    call_tg("sendMessage", {
                        "chat_id": c_id,
                        "text": f"👑 <b>Теперь вашего Личного AI-Директора зовут «{_esc_h(new_name)}»!</b>\n\nЧто делаем дальше?",
                        "parse_mode": "HTML",
                        "reply_markup": build_director_inline_keyboard(default_proj)
                    }, bot_token=b_tok)
                elif data.startswith("dact:") or data.startswith("pdir:"):
                    if director_nlu_engine.handle_director_callback(
                        _get_mesh_ctx(), u, c_id, data, cq_id,
                        is_owner=False, default_project=default_proj,
                        bot_token=b_tok, bot_label=bot_uname,
                        reply_markup_fn=bot_mesh_hub.build_mesh_director_keyboard,
                    ):
                        continue
                    parts = data.split(":", 2)
                    act = parts[1] if len(parts) > 1 else "status"
                    proj = parts[2] if len(parts) > 2 else default_proj
                    call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "⚡ Выполняю..."}, bot_token=b_tok)
                    reply_html = generate_director_reply(u, act, proj)
                    call_tg("sendMessage", {
                        "chat_id": c_id,
                        "text": reply_html,
                        "parse_mode": "HTML",
                        "reply_markup": build_director_inline_keyboard(proj)
                    }, bot_token=b_tok)

            elif "message" in item and any(k in item["message"] for k in ("text", "photo", "document")):
                if "text" in item["message"] and bot_mesh_hub.handle_personal_bot_mesh_interaction(_get_mesh_ctx(), item, u, b_tok, bot_uname, reg):
                    continue
                msg_obj = item["message"]
                media_rel, media_desc = _handle_tg_media_message(msg_obj, b_tok, u_login, default_proj, bot_label=bot_uname)
                text = (msg_obj.get("text") or msg_obj.get("caption") or "").strip()
                if media_rel and not text:
                    director_nlu_engine._persist_bot_work_to_local_and_crm(
                        _get_mesh_ctx(), None, default_proj,
                        f"Telegram вложение ({media_desc}) от @{u_login}",
                        f"Файл сохранён в Офис: {media_rel}", urls=[],
                    )
                    call_tg("sendMessage", {
                        "chat_id": c_id,
                        "text": f"✅ <b>{media_desc} сохранено в Офис!</b>\n• Папка: <code>{_esc_h(default_proj)}</code>\n• Файл: <code>{_esc_h(media_rel)}</code>",
                        "parse_mode": "HTML",
                        "reply_markup": bot_mesh_hub.build_mesh_director_keyboard(default_proj),
                    }, bot_token=b_tok)
                    record_director_action_and_mirror(u, "media_saved", default_proj, f"Сохранено вложение @{u_login}: {media_rel}")
                    continue
                if media_rel and text:
                    text = f"{text}\n\n[Вложение сохранено в Офис: {media_rel}]"
                if not text:
                    continue
                if text.startswith("/start"):
                    reply_html = director_nlu_engine.get_personal_start_greeting(u, default_proj)
                    call_tg("sendMessage", {
                        "chat_id": c_id,
                        "text": reply_html,
                        "parse_mode": "HTML",
                        "reply_markup": bot_mesh_hub.build_mesh_director_keyboard(default_proj)
                    }, bot_token=b_tok)
                    record_director_action_and_mirror(u, "start", default_proj, f"Открыт личный бот пользователем @{u_login}")
                elif text.startswith("/name"):
                    new_name = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
                    if new_name:
                        update_user_bot_control(u_login, "set_director", director_name=new_name)
                        pb["director_name"] = new_name
                        record_director_action_and_mirror(u, "rename", default_proj, f"Смена имени Директора на «{new_name}»")
                        call_tg("sendMessage", {
                            "chat_id": c_id,
                            "text": f"✅ Имя Директора изменено на <b>«{_esc_h(new_name)}»</b>.",
                            "parse_mode": "HTML",
                            "reply_markup": build_director_inline_keyboard(default_proj)
                        }, bot_token=b_tok)
                else:
                    director_nlu_engine.handle_director_text(
                        _get_mesh_ctx(),
                        u,
                        c_id,
                        text,
                        is_owner=False,
                        default_project=default_proj,
                        bot_token=b_tok,
                        bot_label=bot_uname,
                        reply_markup_fn=bot_mesh_hub.build_mesh_director_keyboard,
                    )

    if offsets_changed:
        save_user_bot_offsets(offsets)


def can_chat_update_user_pwd(reg, chat_id, from_user, target_login):
    if is_protected_bot_pwd_account(target_login):
        return False, "protected"
    u = find_user_in_registry(reg, target_login)
    if not u:
        return False, "not_found"
    if is_protected_bot_pwd_account(u.get("login")):
        return False, "protected"
    if is_owner_chat(chat_id) or is_owner_chat((from_user or {}).get("id")):
        return True, "owner"
    matched = find_users_by_tg_sender(reg, from_user or {}, chat_id)
    u_norm = norm_login(u.get("login"))
    for m in matched:
        if norm_login(m.get("login")) == u_norm:
            return True, "self"
    return False, "needs_owner_approval"


def send_member_home_menu(chat_id, from_user):
    reg = fetch_registry(force=True)
    matched = find_users_by_tg_sender(reg, from_user or {}, chat_id)
    fname = (from_user or {}).get("first_name") or (from_user or {}).get("username") or "Участник"
    kb = []
    for m in matched[:4]:
        ml = m.get("login", "")
        kb.append([{"text": f"🔑 Сменить мой пароль: {ml}", "callback_data": f"pwd_pick:{ml[:45]}"}])
    kb.append([{"text": "🔐 Все доступные аккаунты (Смена пароля)", "callback_data": "cmd:passwords"}])
    if matched:
        kb.append([{"text": "🤖 Спросить Личного Директора", "callback_data": "pdir:status:Личный Офис"}])
    kb.append([{"text": "🏢 Открыть Виртуальный Офис (v34.0 Antigravity 2.31)", "url": OFFICE_URL}])

    matched_note = (
        f"• Ваш подтверждённый аккаунт: <code>{_esc_h(', '.join(str(m.get('login', '')) for m in matched))}</code> (прямая смена пароля без ожидания!)\n"
        if matched
        else "• Для мгновенной смены пароля нажмите кнопку ниже или напишите: <code>/reset ваш_логин новый_пароль</code>\n"
    )
    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": (
            f"👋 <b>Добро пожаловать в @unitybusinessbot v34.0 (Antigravity 2.31), {_esc_h(fname)}!</b>\n\n"
            f"Здесь любой участник команды может обновить и сменить свой пароль (кроме защищённого аккаунта главного владельца <code>@unityspiritpartners</code>).\n\n"
            f"{matched_note}"
            f"• Быстрая команда: <code>/reset логин новый_пароль</code>"
        ),
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": kb}
    })


def send_passwords_menu(chat_id, from_user=None):
    reg = fetch_registry(force=True)
    users = [u for u in reg.get("users", []) if not is_protected_bot_pwd_account(u.get("login"))]
    is_owner = is_owner_chat(chat_id) or is_owner_chat((from_user or {}).get("id"))
    matched_logins = set()
    if not is_owner:
        matched_logins = {norm_login(m.get("login")) for m in find_users_by_tg_sender(reg, from_user or {}, chat_id)}

    rows = []
    row = []
    for u in users:
        lg = u.get("login", "")
        lg_norm = norm_login(lg)
        if is_owner:
            icon = "👑" if u.get("role") == "owner" else "👤"
            cb = f"pwd_pick:{lg[:45]}"
        elif lg_norm in matched_logins:
            icon = "🔑"
            cb = f"pwd_pick:{lg[:45]}"
        else:
            icon = "🔐"
            cb = f"ext_pick:{lg[:45]}"
        row.append({"text": f"{icon} {lg}", "callback_data": cb})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "⬅️ Главное меню", "callback_data": "cmd:home"}])

    if is_owner:
        header = (
            "🔐 <b>СМЕНА ПАРОЛЕЙ КОМАНДЫ (@unitybusinessbot v34.0)</b>\n\n"
            "Выберите любой аккаунт ниже (все пользователи, кроме защищённого <code>@unityspiritpartners</code>), чтобы мгновенно обновить пароль:\n"
            "<i>(Либо командой: <code>/reset логин новый_пароль</code>)</i>"
        )
    else:
        header = (
            "🔐 <b>ОБНОВЛЕНИЕ И СМЕНА ПАРОЛЯ (@unitybusinessbot v34.0)</b>\n\n"
            "• 🔑 — ваш подтверждённый аккаунт (пароль меняется мгновенно)\n"
            "• 🔐 — другой аккаунт команды (запрос с 1-клик подтверждением Штаба)\n"
            "<i>🛡️ Главный аккаунт <code>@unityspiritpartners</code> защищён от смены через бота.</i>"
        )
    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": header,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": rows}
    })


def send_bots_control_menu(chat_id):
    reg = fetch_registry(force=True)
    users = reg.get("users", [])
    rows = []
    lines = ["🤖 <b>ЦЕНТР КОНТРОЛЯ ВСЕХ ЛИЧНЫХ БОТОВ И ДИРЕКТОРОВ</b>\n"]
    for u in users:
        lg = u.get("login", "")
        pb = u.get("personal_bot") or {}
        tok = (pb.get("bot_token") or PERSONAL_BOT_TOKENS.get(norm_login(lg)) or "").strip()
        st = pb.get("control_status") or ("active" if tok else "not_configured")
        dir_name = pb.get("director_name") or "Тим"
        b_uname = pb.get("bot_username") or ("подключён" if tok else "не подключён")
        badge = "🟢" if (st == "active" and tok) else ("⏳" if st == "pending_owner" else ("⏸️" if st == "paused" else "⚪"))
        lines.append(f"{badge} <code>@{_esc_h(lg)}</code> · Директор: <b>«{_esc_h(dir_name)}»</b> · Бот: {_esc_h(b_uname)}")
        rows.append([{"text": f"{badge} Управлять ботом @{lg} (Дир: {dir_name})", "callback_data": f"bot_pick:{lg[:45]}"}])
    rows.append([
        {"text": "🤝 Мосты Ботов (A↔B)", "callback_data": "cmd:bridges"},
        {"text": "👁️ Лог Директоров", "callback_data": "cmd:dirlog"}
    ])
    rows.append([
        {"text": "⬅️ Главное меню", "callback_data": "cmd:home"}
    ])
    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": "\n".join(lines) + "\n\n<i>Нажмите на любого сотрудника, чтобы одобрить/поставить на паузу его бота, задать токен или отправить команду через его бота:</i>",
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": rows}
    })


def send_directors_log_menu(chat_id, filter_login=None):
    actions = []
    try:
        log_obj, _ = fetch_json_from_github(GTP_REPO, GTP_TOKEN, "director_actions_log.json", "data")
        actions = log_obj.get("actions", [])
    except Exception:
        pass
    if filter_login:
        fn = norm_login(filter_login)
        actions = [a for a in actions if norm_login(a.get("user_login")) == fn]

    header = f"👁️ <b>ДЕЙСТВИЯ ДИРЕКТОРОВ {_esc_h('@' + filter_login) if filter_login else 'ВСЕЙ КОМАНДЫ'}</b>\n\n"
    if not actions:
        body = "Пока нет зафиксированных действий Директоров в облачном журнале."
    else:
        chunks = []
        for a in actions[:12]:
            ts = _esc_h(str(a.get("time_local") or a.get("ts", "")[11:19]))
            chunks.append(
                f"• <code>{ts}</code> | <b>@{_esc_h(a.get('user_login'))}</b> (Дир: «<b>{_esc_h(a.get('director_name'))}</b>»)\n"
                f"  📁 {_esc_h(a.get('project'))} · <i>{_esc_h(str(a.get('summary',''))[:160])}</i>"
            )
        body = "\n\n".join(chunks)

    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": header + body,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [
            [{"text": "🤖 Контроль ботов команды", "callback_data": "cmd:bots"}, {"text": "🔐 Смена паролей", "callback_data": "cmd:passwords"}],
            [{"text": "🏢 Открыть в Офисе", "url": OFFICE_URL}]
        ]}
    })


def _esc_h(val):
    return str(val or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_hq_home_menu(chat_id, from_user=None):
    if not (is_owner_chat(chat_id) or is_owner_chat((from_user or {}).get("id"))):
        send_member_home_menu(chat_id, from_user or {})
        return

    reg = fetch_registry(force=True)
    users = reg.get("users", [])
    pending = [u for u in users if u.get("status") == "pending"]
    resets = [u for u in users if u.get("pending_reset") and not is_protected_bot_pwd_account(u.get("login"))]
    pend_bots = [u for u in users if (u.get("personal_bot") or {}).get("control_status") == "pending_owner"]
    active_bots = [u for u in users if ((u.get("personal_bot") or {}).get("bot_token") or PERSONAL_BOT_TOKENS.get(norm_login(u.get("login")))) and (u.get("personal_bot") or {}).get("control_status") == "active"]

    txt = (
        f"👑 <b>ГЛАВНЫЙ БОТ ШТАБА (@unitybusinessbot v34.3 · Antigravity 2.31)</b>\n\n"
        f"• Сотрудников в реестре: <b>{len(users)}</b>\n"
        f"• Заявок на вход / сброс пароля: <b>{len(pending) + len(resets)}</b>\n"
        f"• Личных ботов активно / ждут одобрения: <b>{len(active_bots)}</b> / <b>{len(pend_bots)}</b>\n"
        f"• 🤝 Межботовые мосты A↔B и связь со Штабом: <b>активно</b> (<code>/bridges</code>)\n"
        f"• 🧠 <b>Директор Тим NLU (RU/EN):</b> понимает задачи, контекст, ссылки и проверяет статус (<code>/tasks</code>)\n"
        f"• 🛡️ Защита: смена пароля через бота разрешена <b>всем, кроме @unityspiritpartners</b>\n\n"
        f"Выберите действие кнопкой ниже или просто напишите задачу:"
    )
    kb = []
    for u in resets[:4]:
        ul = u.get("login")
        code = (u.get("pending_reset") or {}).get("code", "LOGIN")
        kb.append([
            {"text": f"🔑 Разрешить вход / пароль: {ul} ({code})", "callback_data": f"rst_ok:{ul}"},
            {"text": "🚫 Отклонить", "callback_data": f"rst_no:{ul}"}
        ])
    for p in pending[:4]:
        ul = p.get("login")
        kb.append([{"text": f"✅ Одобрить доступ: {ul}", "callback_data": f"appr:{ul}"}])
    for pb_u in pend_bots[:4]:
        ul = pb_u.get("login")
        kb.append([{"text": f"🤖 Одобрить личный бот: @{ul}", "callback_data": f"bot_appr:{ul}"}])

    kb.append([
        {"text": "🤖 Контроль ботов команды", "callback_data": "cmd:bots"},
        {"text": "🤝 Мосты Ботов (A↔B)", "callback_data": "cmd:bridges"}
    ])
    kb.append([
        {"text": "🔐 Сменить пароль", "callback_data": "cmd:passwords"},
        {"text": "👁️ Что делают их Директора", "callback_data": "cmd:dirlog"}
    ])
    kb.extend([
        [
            {"text": "📋 Проверить задачи (/tasks)", "callback_data": "dact:tasks:Главный Штаб"},
            {"text": "🚀 План Главного Штаба", "callback_data": "pdir:next:Главный Штаб"},
        ],
        [
            {"text": "🔒 Доступ в бот (По запросу)", "callback_data": "cmd:botaccess"},
            {"text": "📊 Аудит работы", "callback_data": "cmd:audit"},
        ],
        [
            {"text": "🛍️ AI-Магазин (Open V3)", "callback_data": "cmd:store_menu"},
            {"text": "💰 Выручка и Лиды", "callback_data": "cmd:revenue"},
        ],
        [
            {"text": "🔎 SEO-Автопилот (/seo)", "callback_data": "cmd:seo"},
            {"text": "⚛️ Antigravity 2.31", "callback_data": "cmd:antigravity"},
        ],
        [
            {"text": "🏢 Главный Офис (v34.6)", "url": OFFICE_URL},
            {"text": "🕸️ Граф Агентов (Live)", "url": "https://globalteampartners.github.io/remote-agents-control/agents_live/"},
        ],
    ])
    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": txt,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": kb}
    })


def _notify_owner_bot_pwd_request(ul, code, req_cid, req_tg_user=""):
    req_label = f"@{_esc_h(req_tg_user)}" if req_tg_user else f"Chat ID <code>{_esc_h(req_cid)}</code>"
    call_tg("sendMessage", {
        "chat_id": OWNER_TG_CHAT_ID,
        "text": (
            f"🔐 <b>Запрос смены пароля через @unitybusinessbot</b>\n\n"
            f"• Целевой аккаунт: <code>{_esc_h(ul)}</code>\n"
            f"• Кто запросил: {req_label}\n"
            f"• Код заявки: <code>{_esc_h(code)}</code>\n\n"
            f"Подтвердите в 1 клик, чтобы пароль обновился и этот Telegram привязался к аккаунту:"
        ),
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[
            {"text": f"✅ Одобрить смену: {ul}", "callback_data": f"rst_ok:{ul}"},
            {"text": "🚫 Отклонить", "callback_data": f"rst_no:{ul}"}
        ]]}
    })


def _default_bot_access():
    return {
        "mode": "request_only",
        "owner_chat_id": str(OWNER_TG_CHAT_ID),
        "approved": {},
        "pending": {},
        "banned": {}
    }


def fetch_bot_access_registry(force=False):
    global _cached_bot_access, _cached_bot_access_ts
    now = time.time()
    if not force and _cached_bot_access is not None and (now - _cached_bot_access_ts < 12):
        return _cached_bot_access
    if os.path.exists(LOCAL_BOT_ACCESS_FILE):
        try:
            with open(LOCAL_BOT_ACCESS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "approved" in data:
                    _cached_bot_access = data
                    _cached_bot_access_ts = now
                    if not force:
                        return data
        except Exception:
            pass
    for repo, tok in [(GTP_REPO, GTP_TOKEN), (U_REPO, U_TOKEN)]:
        try:
            data, _ = fetch_json_from_github(repo, tok, "bot_access_registry.json", "data")
            if isinstance(data, dict):
                data.setdefault("approved", {})
                data.setdefault("pending", {})
                data.setdefault("banned", {})
                _cached_bot_access = data
                _cached_bot_access_ts = now
                with open(LOCAL_BOT_ACCESS_FILE, "w", encoding="utf-8") as lf:
                    json.dump(data, lf, indent=2, ensure_ascii=False)
                return data
        except urllib.error.HTTPError as he:
            if he.code == 404:
                continue
        except Exception:
            pass
    data = _cached_bot_access or _default_bot_access()
    _cached_bot_access = data
    _cached_bot_access_ts = now
    return data


def mutate_bot_access_registry(mutator_fn, commit_msg):
    global _cached_bot_access, _cached_bot_access_ts
    ok, latest = mutate_json_across_repos(
        "bot_access_registry.json",
        mutator_fn,
        commit_msg,
        branches=["data"],
        default_factory=_default_bot_access,
    )
    if ok and latest is not None:
        _cached_bot_access = latest
        _cached_bot_access_ts = time.time()
        try:
            with open(LOCAL_BOT_ACCESS_FILE, "w", encoding="utf-8") as lf:
                json.dump(latest, lf, indent=2, ensure_ascii=False)
        except Exception:
            pass
    return ok, latest


def check_bot_entry_gate(chat_id, from_user, message_text="", is_callback=False, cq_id=None):
    c_id = str(chat_id)
    if is_owner_chat(c_id) or is_owner_chat((from_user or {}).get("id")):
        return True
    access = fetch_bot_access_registry(force=False)
    if c_id in (access.get("approved") or {}):
        return True
    if c_id in (access.get("banned") or {}):
        if is_callback and cq_id:
            call_tg("answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "🚫 Доступ к @unitybusinessbot закрыт Главным Штабом (@unityspiritpartners).",
                "show_alert": True
            })
        else:
            call_tg("sendMessage", {
                "chat_id": c_id,
                "text": "🚫 <b>Доступ к @unitybusinessbot закрыт.</b>\n\nОбратитесь напрямую к @unityspiritpartners.",
                "parse_mode": "HTML"
            })
        return False

    uname = (from_user or {}).get("username") or ""
    fname = (from_user or {}).get("first_name") or (from_user or {}).get("last_name") or "Пользователь"
    now_ts = time.time()
    prev_notified = float(((access.get("pending") or {}).get(c_id) or {}).get("last_notified_ts") or 0)
    should_notify_owner = (now_ts - prev_notified) >= 120

    def _mutate(obj):
        obj.setdefault("pending", {})
        entry = obj["pending"].get(c_id) or {}
        entry.update({
            "chat_id": c_id,
            "username": uname,
            "first_name": fname,
            "last_text": (message_text or "")[:180],
            "requested_at": entry.get("requested_at") or datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "last_seen_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "last_notified_ts": now_ts if should_notify_owner else entry.get("last_notified_ts", 0),
        })
        obj["pending"][c_id] = entry
        return True

    mutate_bot_access_registry(_mutate, f"bot-gate: pending request from chat_id {c_id} [v34.0]")

    if should_notify_owner:
        who = f"@{_esc_h(uname)} ({_esc_h(fname)})" if uname else _esc_h(fname)
        msg_preview = _esc_h((message_text or "/start")[:180])
        call_tg("sendMessage", {
            "chat_id": OWNER_TG_CHAT_ID,
            "text": (
                f"🔒 <b>ЗАПРОС НА ВХОД В @unitybusinessbot</b>\n\n"
                f"• Пользователь: {who}\n"
                f"• Chat ID: <code>{_esc_h(c_id)}</code>\n"
                f"• Текст запроса: <code>{msg_preview}</code>\n\n"
                f"<i>Бот работает в режиме «только по одобрению @unityspiritpartners». Без вашего нажатия ниже пользователь не увидит меню, задачи и смену паролей.</i>"
            ),
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[
                {"text": "✅ Одобрить вход в бот", "callback_data": f"bot_gate_ok:{c_id}"},
                {"text": "🚫 Отклонить", "callback_data": f"bot_gate_ban:{c_id}"}
            ]]}
        })

    if is_callback and cq_id:
        call_tg("answerCallbackQuery", {
            "callback_query_id": cq_id,
            "text": "⏳ Вход в бот только по запросу и одобрению @unityspiritpartners.",
            "show_alert": True
        })
    else:
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": (
                f"🔒 <b>Вход в @unitybusinessbot — только по личному запросу к @unityspiritpartners</b>\n\n"
                f"Ваш запрос на доступ (Chat ID: <code>{_esc_h(c_id)}</code>) отправлен владельцу <b>@unityspiritpartners</b>.\n"
                f"Как только @unityspiritpartners одобрит заявку, вы получите уведомление сюда."
            ),
            "parse_mode": "HTML"
        })
    return False


def send_bot_access_menu(chat_id):
    access = fetch_bot_access_registry(force=True)
    pending = access.get("pending") or {}
    approved = access.get("approved") or {}
    banned = access.get("banned") or {}
    lines = [
        "🔒 <b>ДОСТУП В @unitybusinessbot (ТОЛЬКО ПО ЗАПРОСУ)</b>\n",
        f"• Режим: <b>Только по одобрению @unityspiritpartners</b>",
        f"• Ожидают решения: <b>{len(pending)}</b>",
        f"• Одобрено: <b>{len(approved)}</b>",
        f"• Отклонено: <b>{len(banned)}</b>\n"
    ]
    kb = []
    if pending:
        lines.append("<b>⏳ Новые запросы:</b>")
        for cid, info in list(pending.items())[:8]:
            lbl = f"@{info.get('username')}" if info.get("username") else (info.get("first_name") or cid)
            lines.append(f"• {_esc_h(lbl)} (<code>{_esc_h(cid)}</code>)")
            kb.append([
                {"text": f"✅ Одобрить: {lbl[:20]}", "callback_data": f"bot_gate_ok:{cid}"},
                {"text": "🚫 Отклонить", "callback_data": f"bot_gate_ban:{cid}"}
            ])
        lines.append("")
    if approved:
        lines.append("<b>✅ Имеют доступ в бот:</b>")
        for cid, info in list(approved.items())[:10]:
            lbl = f"@{info.get('username')}" if info.get("username") else (info.get("first_name") or cid)
            lines.append(f"• {_esc_h(lbl)} (<code>{_esc_h(cid)}</code>)")
            kb.append([{"text": f"🗑️ Закрыть доступ: {lbl[:24]}", "callback_data": f"bot_gate_rev:{cid}"}])
    kb.append([{"text": "🔄 Обновить список", "callback_data": "cmd:botaccess"}, {"text": "⬅️ Главное меню", "callback_data": "cmd:home"}])
    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": "\n".join(lines),
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": kb}
    })


def touch_local_heartbeat_if_due(force=False):
    global _last_heartbeat_push_ts
    now = time.time()
    if not force and (now - _last_heartbeat_push_ts < 60):
        return
    _last_heartbeat_push_ts = now
    hb_payload = {
        "ts": now,
        "iso": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "version": "34.7",
        "status": "online",
        "tg_offset": get_persisted_offset(),
    }
    def _write_hb(obj):
        obj.clear()
        obj.update(hb_payload)
        return True
    try:
        mutate_json_across_repos("local_daemon_heartbeat.json", _write_hb, "Heartbeat local daemon [v34.3]", branches=["data"], default_factory=dict)
    except Exception:
        pass


def send_antigravity_status_menu(chat_id):
    try:
        tel_raw, _ = fetch_json_from_github(GTP_REPO, GTP_TOKEN, "cloud_agents_telemetry.json", branch="data")
        tel = tel_raw if isinstance(tel_raw, dict) else {}
    except Exception:
        tel = {}
    try:
        mem_raw, _ = fetch_json_from_github(GTP_REPO, GTP_TOKEN, "office_memory.json", branch="data")
        mem = mem_raw if isinstance(mem_raw, dict) else {}
    except Exception:
        mem = {}
    llm_ok = bool(tel.get("llm_ready")) or bool(GEMINI_API_KEY)
    lines = [
        "⚛️ <b>ANTIGRAVITY 2.31 · GEMINI 4 ORCHESTRATOR (v34.0)</b>\n",
        f"• <b>Маршрутизация:</b> <code>{_esc_h(tel.get('orchestrator_mode') or 'Argon <-> Carbon <-> Hydrogen (1M context)')}</code>",
        f"• <b>Движок:</b> <code>{_esc_h(tel.get('model_engine') or 'gemini-4-argon / gemini-4-hydrogen')}</code>",
        f"• <b>Облако 24/7:</b> <code>{_esc_h(tel.get('status') or 'online_24_7')}</code> (цикл #{int(tel.get('cycle_count') or 0)})",
        f"• <b>Последний цикл:</b> <code>{_esc_h(tel.get('last_cycle_iso') or '—')}</code>",
        f"• <b>Память офиса:</b> v<code>{_esc_h(mem.get('version') or '—')}</code> · <b>{len(mem.get('learned_facts') or [])}</b> фактов · <b>{len(mem.get('studied_urls') or [])}</b> ссылок",
        f"• <b>Локальный Poller v34.0:</b> 🟢 Онлайн (только @unitybusinessbot; устаревшие PC-воркеры KHL/Freqtrade остановлены)",
        f"• <b>Ключ GEMINI_API_KEY:</b> {'🟢 Активен' if llm_ok else '🟡 Не задан в GitHub Secrets — работает structured analyzer'}",
    ]
    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": "\n".join(lines),
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [
            [{"text": "📊 Аудит сохранённой работы (/audit)", "callback_data": "cmd:audit"}],
            [{"text": "🏢 Открыть Офис (v34.0)", "url": OFFICE_URL}, {"text": "⬅️ Меню", "callback_data": "cmd:home"}]
        ]}
    })


def send_saved_work_audit_menu(chat_id):
    try:
        bd_raw, _ = fetch_json_from_github(GTP_REPO, GTP_TOKEN, "board.json", branch="data")
        bd = bd_raw if isinstance(bd_raw, dict) else {}
    except Exception:
        bd = {}
    try:
        mem_raw, _ = fetch_json_from_github(GTP_REPO, GTP_TOKEN, "office_memory.json", branch="data")
        mem = mem_raw if isinstance(mem_raw, dict) else {}
    except Exception:
        mem = {}
    cards = bd.get("cards") if isinstance(bd.get("cards"), dict) else {}
    by_status = {}
    for c in cards.values():
        if isinstance(c, dict):
            st = c.get("status") or "unknown"
            by_status[st] = by_status.get(st, 0) + 1
    open_issues_cnt = "—"
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{GTP_REPO}/issues?state=open&per_page=40",
            headers={"Authorization": f"token {GTP_TOKEN}", "Accept": "application/vnd.github.v3+json", "User-Agent": "GSG-Bot-v34"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            iss = json.loads(resp.read().decode("utf-8"))
            open_issues_cnt = len([x for x in iss if isinstance(x, dict) and not x.get("pull_request")])
    except Exception:
        pass
    st_str = ", ".join(f"{_esc_h(k)}: <b>{v}</b>" for k, v in sorted(by_status.items(), key=lambda kv: -kv[1])) or "нет данных"
    lines = [
        "📊 <b>АУДИТ СОХРАНЁННОЙ РАБОТЫ И СИСТЕМЫ (v34.0)</b>\n",
        f"• <b>Карточки в board.json:</b> <b>{len(cards)}</b> ({st_str})",
        f"• <b>Открытые задачи GitHub HQ:</b> <b>{_esc_h(open_issues_cnt)}</b>",
        f"• <b>Память office_memory.json:</b> v<code>{_esc_h(mem.get('version') or '—')}</code> | цикл #{int(mem.get('cycle_count') or 0)} | <b>{len(mem.get('learned_facts') or [])}</b> фактов | <b>{len(mem.get('studied_urls') or [])}</b> URL | ошибок: <b>{len(mem.get('failed_urls') or {})}</b>",
        f"• <b>Штаб Poller v34.0:</b> 🟢 Работает с реальным heartbeat",
    ]
    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": "\n".join(lines),
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [
            [{"text": "⚛️ Статус Antigravity 2.31", "callback_data": "cmd:antigravity"}],
            [{"text": "🏢 Открыть Офис (v34.0)", "url": OFFICE_URL}, {"text": "⬅️ Меню", "callback_data": "cmd:home"}]
        ]}
    })


def handle_hq_callback(cq):
    cq_id = cq["id"]
    data = cq.get("data", "")
    from_user = cq.get("from", {})
    user_name = from_user.get("username") or from_user.get("first_name") or "Пользователь"
    c_id = str(cq.get("message", {}).get("chat", {}).get("id") or from_user.get("id") or OWNER_TG_CHAT_ID)
    is_owner = is_owner_chat(c_id) or is_owner_chat(from_user.get("id"))

    admin_prefixes = ("cmd:bots", "cmd:bridges", "cmd:dirlog", "cmd:botaccess", "cmd:antigravity", "cmd:audit", "appr:", "rst_ok:", "rst_no:", "bot_gate_ok:", "bot_gate_ban:", "bot_gate_rev:", "bot_pick:", "bot_appr:", "bot_pause:", "bot_ban:", "bot_asktok:", "bot_askdir:", "bot_askmsg:", "bot_log:", "brg_")
    if any(data == p or data.startswith(p) for p in admin_prefixes) and not is_owner:
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "⛔ Эта функция доступна только Главному Штабу.", "show_alert": True})
        return

    if not is_owner and not check_bot_entry_gate(c_id, from_user, message_text=f"cb:{data}", is_callback=True, cq_id=cq_id):
        return

    if bot_mesh_hub.handle_hq_mesh_callback(_get_mesh_ctx(), cq):
        return

    if data == "cmd:home" or data == "cmd:pending":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🔄 Обновляю..."})
        send_hq_home_menu(c_id, from_user=from_user)
    elif data == "cmd:antigravity":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "⚛️ Загружаю Antigravity 2.31..."})
        send_antigravity_status_menu(c_id)
    elif data == "cmd:audit":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "📊 Считаю аудит работы..."})
        send_saved_work_audit_menu(c_id)
    elif data == "cmd:botaccess":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🔒 Загружаю список доступа..."})
        send_bot_access_menu(c_id)
    elif data.startswith("bot_gate_ok:"):
        target_cid = data.split("bot_gate_ok:", 1)[1].strip()
        user_rec = {}
        def _approve_gate(obj):
            obj.setdefault("approved", {})
            obj.setdefault("pending", {})
            obj.setdefault("banned", {})
            src = obj["pending"].pop(target_cid, None) or obj["banned"].pop(target_cid, None) or {"chat_id": target_cid}
            src["approved_by"] = "@unityspiritpartners"
            src["approved_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            obj["approved"][target_cid] = src
            user_rec.update(src)
            return True
        ok, _ = mutate_bot_access_registry(_approve_gate, f"bot-gate: approve {target_cid} by @unityspiritpartners [v34.0]")
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "✅ Вход в бот одобрен!" if ok else "⚠️ Ошибка сохранения"})
        if ok:
            lbl = f"@{_esc_h(user_rec.get('username'))}" if user_rec.get("username") else _esc_h(user_rec.get("first_name") or target_cid)
            call_tg("sendMessage", {
                "chat_id": c_id,
                "text": f"✅ Доступ в <b>@unitybusinessbot</b> для {lbl} (<code>{_esc_h(target_cid)}</code>) <b>одобрен</b>.",
                "parse_mode": "HTML"
            })
            call_tg("sendMessage", {
                "chat_id": target_cid,
                "text": "✅ <b>@unityspiritpartners одобрил ваш вход в @unitybusinessbot!</b>\n\nНажмите /start для открытия вашего меню.",
                "parse_mode": "HTML"
            })
        return
    elif data.startswith("bot_gate_ban:"):
        target_cid = data.split("bot_gate_ban:", 1)[1].strip()
        def _ban_gate(obj):
            obj.setdefault("approved", {})
            obj.setdefault("pending", {})
            obj.setdefault("banned", {})
            src = obj["pending"].pop(target_cid, None) or obj["approved"].pop(target_cid, None) or {"chat_id": target_cid}
            src["banned_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            obj["banned"][target_cid] = src
            return True
        ok, _ = mutate_bot_access_registry(_ban_gate, f"bot-gate: ban {target_cid} by @unityspiritpartners [v34.0]")
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🚫 Доступ отклонён"})
        if ok:
            call_tg("sendMessage", {"chat_id": c_id, "text": f"🚫 Доступ в бот для Chat ID <code>{_esc_h(target_cid)}</code> отклонён.", "parse_mode": "HTML"})
            call_tg("sendMessage", {"chat_id": target_cid, "text": "🚫 Запрос на вход в @unitybusinessbot отклонён @unityspiritpartners."})
        return
    elif data.startswith("bot_gate_rev:"):
        target_cid = data.split("bot_gate_rev:", 1)[1].strip()
        def _rev_gate(obj):
            obj.setdefault("approved", {}).pop(target_cid, None)
            obj.setdefault("pending", {}).pop(target_cid, None)
            return True
        ok, _ = mutate_bot_access_registry(_rev_gate, f"bot-gate: revoke {target_cid} by @unityspiritpartners [v34.0]")
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🗑️ Доступ закрыт" if ok else "⚠️ Ошибка"})
        if ok:
            send_bot_access_menu(c_id)
        return
    elif data == "cmd:passwords":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🔐 Смена паролей"})
        send_passwords_menu(c_id, from_user=from_user)
    elif data == "cmd:bots":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🤖 Боты команды"})
        send_bots_control_menu(c_id)
    elif data == "cmd:dirlog":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "👁️ Загружаю журнал..."})
        send_directors_log_menu(c_id)
    elif data == "cmd:store_menu":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🛍️ Open V3 AI-Билдер"})
        send_store_builder_menu(c_id)
    elif data == "cmd:revenue":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "💰 Сводка выручки Офиса"})
        send_revenue_summary_menu(c_id)
    elif data == "cmd:seo":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🔎 Запускаю Claude SEO-аудит..."})
        _trigger_seo_from_tg(c_id, "")
    elif data.startswith("store_quick:"):
        niche_Pick = data.split("store_quick:", 1)[1].strip()
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"🛍️ Собираю магазин: {niche_Pick[:25]}..."})
        _execute_store_build_from_tg(c_id, niche_Pick)
    elif data.startswith("pwd_pick:"):
        ul = data.split("pwd_pick:", 1)[1]
        reg = fetch_registry(force=True)
        allowed, reason = can_chat_update_user_pwd(reg, c_id, from_user, ul)
        if reason == "protected":
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🛡️ Аккаунт @unityspiritpartners защищён — его пароль нельзя менять через бота.", "show_alert": True})
            return
        if reason == "not_found":
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"⚠️ Аккаунт {ul} не найден.", "show_alert": True})
            return
        if not allowed:
            data = f"ext_pick:{ul}"
        else:
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Пароль: {ul}"})
            rows = [
                [{"text": f"✏️ Ввести свой новый пароль для {ul}", "callback_data": f"pwd_ask:{ul}"}],
                [{"text": f"⚡ Сгенерировать пароль в 1 клик для {ul}", "callback_data": f"pwd_gen:{ul}"}],
            ]
            if is_owner:
                rows.append([{"text": f"✅ Разрешить вход / pending_reset ({ul})", "callback_data": f"rst_ok:{ul}"}])
            rows.append([{"text": "⬅️ Назад к списку", "callback_data": "cmd:passwords"}])
            call_tg("sendMessage", {
                "chat_id": c_id,
                "text": f"🔐 <b>Смена пароля для аккаунта <code>{_esc_h(ul)}</code></b>\n\nВыберите способ смены пароля прямо здесь в боте:",
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": rows}
            })
            return
    if data.startswith("ext_pick:"):
        ul = data.split("ext_pick:", 1)[1]
        if is_protected_bot_pwd_account(ul):
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🛡️ Аккаунт @unityspiritpartners защищён.", "show_alert": True})
            return
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Запрос для {ul}"})
        kb = {"inline_keyboard": [
            [{"text": f"✏️ Ввести новый пароль для {ul}", "callback_data": f"ext_ask:{ul}"}],
            [{"text": f"⚡ Сгенерировать пароль и отправить в Штаб ({ul})", "callback_data": f"ext_gen:{ul}"}],
            [{"text": "⬅️ Назад к списку", "callback_data": "cmd:passwords"}]
        ]}
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": (
                f"🔐 <b>Запрос на обновление пароля для <code>{_esc_h(ul)}</code></b>\n\n"
                f"Ваш Telegram (<code>@{_esc_h(from_user.get('username') or c_id)}</code>) пока не привязан к <code>{_esc_h(ul)}</code>.\n"
                f"Укажите новый пароль — заявка в 1 клик уйдёт Главному Штабу на подтверждение, после чего этот аккаунт откроется для прямой смены."
            ),
            "parse_mode": "HTML",
            "reply_markup": kb
        })
    elif data.startswith("pwd_ask:"):
        ul = data.split("pwd_ask:", 1)[1]
        reg = fetch_registry(force=True)
        allowed, reason = can_chat_update_user_pwd(reg, c_id, from_user, ul)
        if reason == "protected":
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🛡️ Аккаунт @unityspiritpartners защищён — его пароль нельзя менять через бота.", "show_alert": True})
            return
        if not allowed:
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "⚠️ Выберите ext_ask для подтверждения Штабом.", "show_alert": True})
            return
        _chat_states[c_id] = {"mode": "set_pwd", "login": ul, "ts": time.time()}
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Напишите новый пароль для {ul}"})
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": f"✏️ <b>Отправьте новым сообщением пароль для <code>{_esc_h(ul)}</code></b> (от 4 символов):\n\n<i>Например: <code>MySecret2026</code> (или /cancel для отмены)</i>",
            "parse_mode": "HTML"
        })
    elif data.startswith("ext_ask:"):
        ul = data.split("ext_ask:", 1)[1]
        if is_protected_bot_pwd_account(ul):
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🛡️ Аккаунт @unityspiritpartners защищён.", "show_alert": True})
            return
        _chat_states[c_id] = {"mode": "ext_set_pwd", "login": ul, "tg_user": from_user.get("username") or "", "ts": time.time()}
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Напишите новый пароль для {ul}"})
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": f"✏️ <b>Напишите желаемый новый пароль для <code>{_esc_h(ul)}</code></b> (от 4 символов):\n\n<i>Заявка на активацию уйдёт в Главный Штаб (или /cancel для отмены).</i>",
            "parse_mode": "HTML"
        })
    elif data.startswith("pwd_gen:"):
        ul = data.split("pwd_gen:", 1)[1]
        reg = fetch_registry(force=True)
        allowed, reason = can_chat_update_user_pwd(reg, c_id, from_user, ul)
        if reason == "protected":
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🛡️ Аккаунт @unityspiritpartners защищён — его пароль нельзя менять через бота.", "show_alert": True})
            return
        if not allowed:
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "⚠️ Недостаточно прав для прямой генерации.", "show_alert": True})
            return
        rnd_digits = "".join(secrets.choice(string.digits) for _ in range(4))
        rnd_word = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(3))
        new_pwd = f"GSG-{rnd_digits}-{rnd_word}"
        ok = set_user_password_in_github(ul, new_pwd, verified_chat_id=None if is_owner else c_id)
        if not ok:
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"❌ Ошибка записи для {ul}. Повторите.", "show_alert": True})
            return
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"✅ Новый пароль для {ul} сохранён!", "show_alert": True})
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": (
                f"✅ <b>НОВЫЙ ПАРОЛЬ УСТАНОВЛЕН ЧЕРЕЗ БОТА!</b>\n\n"
                f"• Логин: <code>{_esc_h(ul)}</code>\n"
                f"• Новый пароль: <code>{_esc_h(new_pwd)}</code> <i>(нажмите, чтобы скопировать)</i>\n"
                f"• Статус: <b>✅ Активен во всех репозиториях</b>"
            ),
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[{"text": "🏢 Войти в Офис (v34.0)", "url": OFFICE_URL}]]}
        })
        if not is_owner:
            call_tg("sendMessage", {
                "chat_id": OWNER_TG_CHAT_ID,
                "text": f"🔐 <b>Обновление пароля участником:</b> <code>{_esc_h(ul)}</code> (@{_esc_h(user_name)}) установил новый пароль через @unitybusinessbot.",
                "parse_mode": "HTML"
            })
    elif data.startswith("ext_gen:"):
        ul = data.split("ext_gen:", 1)[1]
        if is_protected_bot_pwd_account(ul):
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🛡️ Аккаунт @unityspiritpartners защищён.", "show_alert": True})
            return
        rnd_digits = "".join(secrets.choice(string.digits) for _ in range(4))
        rnd_word = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(3))
        new_pwd = f"GSG-{rnd_digits}-{rnd_word}"
        tg_u = from_user.get("username") or ""
        ok, code = save_pending_bot_reset_in_github(ul, new_pwd, c_id, tg_u)
        if not ok:
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"⚠️ Не удалось создать заявку для {ul}.", "show_alert": True})
            return
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"⏳ Заявка ({code}) отправлена в Штаб!", "show_alert": True})
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": (
                f"⏳ <b>ЗАЯВКА НА СМЕНУ ПАРОЛЯ ОТПРАВЛЕНА В ШТАБ!</b>\n\n"
                f"• Аккаунт: <code>{_esc_h(ul)}</code>\n"
                f"• Ваш новый пароль: <code>{_esc_h(new_pwd)}</code>\n"
                f"• Код подтверждения: <code>{_esc_h(code)}</code>\n\n"
                f"<i>Как только @unityspiritpartners нажмёт «Одобрить» в @unitybusinessbot, вы сразу получите уведомление сюда и сможете войти!</i>"
            ),
            "parse_mode": "HTML"
        })
        _notify_owner_bot_pwd_request(ul, code, c_id, tg_u)
    elif data.startswith("appr:"):
        ul = data.split("appr:", 1)[1]
        ok = approve_user_in_github(ul)
        if not ok:
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"⚠️ Не удалось обновить {ul} (проверьте интернет).", "show_alert": True})
            return
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"✅ Доступ для {ul} одобрен!", "show_alert": True})
        if "message" in cq:
            orig_msg = cq["message"]
            call_tg("editMessageText", {
                "chat_id": orig_msg["chat"]["id"],
                "message_id": orig_msg["message_id"],
                "text": (
                    f"👑 <b>Авторизация в Global Spirit Group ★ Виртуальный Офис</b>\n\n"
                    f"• Пользователь: <code>{_esc_h(ul)}</code>\n"
                    f"• Статус: <b>✅ ОДОБРЕНО ЧЕРЕЗ @unitybusinessbot</b>\n"
                    f"• Подтвердил: @{_esc_h(user_name)}"
                ),
                "parse_mode": "HTML"
            })
    elif data.startswith("rst_ok:"):
        ul = data.split("rst_ok:", 1)[1]
        if is_protected_bot_pwd_account(ul):
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🛡️ Аккаунт @unityspiritpartners защищён от сброса через бота.", "show_alert": True})
            return
        reg_pre = fetch_registry(force=True)
        u_pre = find_user_in_registry(reg_pre, ul)
        req_cid = str(((u_pre or {}).get("pending_reset") or {}).get("requested_chat_id") or "")
        ok = handle_password_reset_in_github(ul, True)
        if not ok:
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"⚠️ Запрос для {ul} не найден или ошибка сети. Попробуйте задать пароль вручную в /passwords.", "show_alert": True})
            return
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"🔑 Вход и пароль для {ul} подтверждены!", "show_alert": True})
        if "message" in cq:
            orig_msg = cq["message"]
            call_tg("editMessageText", {
                "chat_id": orig_msg["chat"]["id"],
                "message_id": orig_msg["message_id"],
                "text": (
                    f"🔑 <b>Подтверждение входа / сброса пароля</b>\n\n"
                    f"• Аккаунт: <code>{_esc_h(ul)}</code>\n"
                    f"• Статус: <b>✅ ПАРОЛЬ ОБНОВЛЁН И ВХОД РАЗРЕШЁН</b>\n"
                    f"• Подтвердил: @{_esc_h(user_name)}\n"
                    f"• Страница пользователя разблокируется автоматически!"
                ),
                "parse_mode": "HTML"
            })
        if req_cid and not is_owner_chat(req_cid):
            call_tg("sendMessage", {
                "chat_id": req_cid,
                "text": (
                    f"✅ <b>ПАРОЛЬ АКТИВИРОВАН ГЛАВНЫМ ШТАБОМ!</b>\n\n"
                    f"• Аккаунт: <code>{_esc_h(ul)}</code>\n"
                    f"• Ваш Telegram теперь привязан для быстрой смены пароля в 1 клик.\n"
                    f"• Можете входить в Виртуальный Офис прямо сейчас!"
                ),
                "parse_mode": "HTML",
                "reply_markup": {"inline_keyboard": [[{"text": "🏢 Войти в Офис (v34.0)", "url": OFFICE_URL}]]}
            })
    elif data.startswith("rst_no:"):
        ul = data.split("rst_no:", 1)[1]
        if is_protected_bot_pwd_account(ul):
            call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🛡️ Аккаунт @unityspiritpartners защищён.", "show_alert": True})
            return
        reg_pre = fetch_registry(force=True)
        u_pre = find_user_in_registry(reg_pre, ul)
        req_cid = str(((u_pre or {}).get("pending_reset") or {}).get("requested_chat_id") or "")
        handle_password_reset_in_github(ul, False)
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"🚫 Запрос для {ul} отклонён.", "show_alert": True})
        if "message" in cq:
            orig_msg = cq["message"]
            call_tg("editMessageText", {
                "chat_id": orig_msg["chat"]["id"],
                "message_id": orig_msg["message_id"],
                "text": f"🚫 <b>Попытка входа / смены пароля для <code>{_esc_h(ul)}</code> отклонена (@{_esc_h(user_name)}).</b>",
                "parse_mode": "HTML"
            })
        if req_cid and not is_owner_chat(req_cid):
            call_tg("sendMessage", {
                "chat_id": req_cid,
                "text": f"🚫 Заявка на смену пароля для <code>{_esc_h(ul)}</code> отклонена Главным Штабом.",
                "parse_mode": "HTML"
            })
    elif data.startswith("bot_pick:"):
        ul = data.split("bot_pick:", 1)[1]
        reg = fetch_registry(force=True)
        u = find_user_in_registry(reg, ul) or {}
        pb = u.get("personal_bot") or {}
        dir_n = pb.get("director_name") or "Тим"
        st = pb.get("control_status") or ("active" if pb.get("bot_token") else "not_configured")
        tok_mask = (pb.get("bot_token")[:12] + "...") if pb.get("bot_token") else "не задан"
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Управление ботом @{ul}"})
        kb = {"inline_keyboard": [
            [
                {"text": "✅ Одобрить / Включить", "callback_data": f"bot_appr:{ul}"},
                {"text": "⏸️ Пауза", "callback_data": f"bot_pause:{ul}"},
                {"text": "🚫 Отключить", "callback_data": f"bot_ban:{ul}"}
            ],
            [
                {"text": "🔑 Назначить Bot Token", "callback_data": f"bot_asktok:{ul}"},
                {"text": "🏷️ Сменить имя Директора", "callback_data": f"bot_askdir:{ul}"}
            ],
            [
                {"text": "💬 Отправить команду в бот", "callback_data": f"bot_askmsg:{ul}"},
                {"text": "👁️ Лог их Директора", "callback_data": f"bot_log:{ul}"}
            ],
            [{"text": "⬅️ Все боты команды", "callback_data": "cmd:bots"}]
        ]}
        b_un = str(pb.get("bot_username") or "").strip().lstrip("@")
        b_link = f"@{_esc_h(b_un)} (<a href=\"https://t.me/{_esc_h(b_un)}\">t.me/{_esc_h(b_un)}</a>)" if b_un else "— <i>токен не подключён (нажмите «🔑 Назначить Bot Token» после создания в @BotFather)</i>"
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": (
                f"🤖 <b>КОНТРОЛЬ БОТА СОТРУДНИКА <code>@{_esc_h(ul)}</code></b>\n\n"
                f"• <b>Статус контроля:</b> <code>{_esc_h(st)}</code>\n"
                f"• <b>Имя Директора:</b> «<b>{_esc_h(dir_n)}</b>»\n"
                f"• <b>Бот:</b> {b_link}\n"
                f"• <b>Токен:</b> <code>{_esc_h(tok_mask)}</code> | <b>Chat ID:</b> <code>{_esc_h(pb.get('chat_id') or 'не привязан')}</code>"
            ),
            "parse_mode": "HTML",
            "reply_markup": kb
        })
    elif data.startswith("bot_appr:"):
        ul = data.split("bot_appr:", 1)[1]
        ok, _ = update_user_bot_control(ul, "approve")
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"✅ Бот @{ul} одобрен и включён!" if ok else "⚠️ Пользователь не найден", "show_alert": True})
        if ok:
            reg = fetch_registry(force=True)
            u = find_user_in_registry(reg, ul)
            pb = (u or {}).get("personal_bot") or {}
            if pb.get("bot_token") and pb.get("chat_id"):
                d_n = pb.get("director_name") or "Тим"
                call_tg("sendMessage", {
                    "chat_id": pb["chat_id"],
                    "text": f"🟢 <b>Главный Штаб (@unityspiritpartners) одобрил и подключил вашего Личного Директора «{_esc_h(d_n)}»!</b>\n\nТеперь вы можете управлять задачами прямо отсюда:",
                    "parse_mode": "HTML",
                    "reply_markup": build_director_inline_keyboard(f"Личный Офис: @{ul}")
                }, bot_token=pb["bot_token"])
            call_tg("sendMessage", {"chat_id": c_id, "text": f"🟢 Бот пользователя <code>@{_esc_h(ul)}</code> одобрен и запущен в работу.", "parse_mode": "HTML"})
    elif data.startswith("bot_pause:"):
        ul = data.split("bot_pause:", 1)[1]
        ok, _ = update_user_bot_control(ul, "pause")
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"⏸️ Бот @{ul} поставлен на паузу." if ok else "⚠️ Ошибка", "show_alert": True})
        call_tg("sendMessage", {"chat_id": c_id, "text": f"⏸️ Личный бот сотрудника <code>@{_esc_h(ul)}</code> поставлен на паузу.", "parse_mode": "HTML"})
    elif data.startswith("bot_ban:"):
        ul = data.split("bot_ban:", 1)[1]
        ok, _ = update_user_bot_control(ul, "disable")
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"🚫 Бот @{ul} отключён." if ok else "⚠️ Ошибка", "show_alert": True})
        call_tg("sendMessage", {"chat_id": c_id, "text": f"🚫 Личный бот сотрудника <code>@{_esc_h(ul)}</code> отключён.", "parse_mode": "HTML"})
    elif data.startswith("bot_asktok:"):
        ul = data.split("bot_asktok:", 1)[1]
        _chat_states[c_id] = {"mode": "set_bot_token", "login": ul, "ts": time.time()}
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Отправьте Bot Token для @{ul}"})
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": f"🔑 <b>Отправьте токен бота из @BotFather для сотрудника <code>@{_esc_h(ul)}</code></b>:\n<i>Бот автоматически проверится через Telegram API, привяжется к @{_esc_h(ul)} и начнёт работу под вашим контролем.</i>",
            "parse_mode": "HTML"
        })
    elif data.startswith("bot_askdir:"):
        ul = data.split("bot_askdir:", 1)[1]
        _chat_states[c_id] = {"mode": "set_bot_director", "login": ul, "ts": time.time()}
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Введите имя Директора для @{ul}"})
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": f"🏷️ <b>Отправьте новое имя Личного Директора для сотрудника <code>@{_esc_h(ul)}</code>:</b>",
            "parse_mode": "HTML"
        })
    elif data.startswith("bot_askmsg:"):
        ul = data.split("bot_askmsg:", 1)[1]
        _chat_states[c_id] = {"mode": "send_bot_msg", "login": ul, "ts": time.time()}
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Введите сообщение в бот @{ul}"})
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": f"💬 <b>Напишите сообщение или задание для отправки прямо в личный бот сотрудника <code>@{_esc_h(ul)}</code>:</b>",
            "parse_mode": "HTML"
        })
    elif data.startswith("bot_log:"):
        ul = data.split("bot_log:", 1)[1]
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Лог Директора @{ul}"})
        send_directors_log_menu(c_id, filter_login=ul)
    elif data.startswith("dact:") or data.startswith("pdir:"):
        if is_owner:
            actor_u = {"login": "unityspiritpartners", "full_name": "Власлав (Главный Штаб)", "personal_bot": {"director_name": "Тим"}}
            def_proj = "Главный Штаб"
        else:
            reg_d = fetch_registry(force=False)
            matched_d = find_users_by_tg_sender(reg_d, from_user, c_id)
            actor_u = matched_d[0] if matched_d else {"login": user_name, "full_name": user_name, "personal_bot": {"director_name": "Тим"}}
            assg = [p for p in (actor_u.get("assigned_projects") or []) if p != "*"]
            def_proj = assg[0] if assg else f"Личный Офис: @{actor_u.get('login')}"
        if director_nlu_engine.handle_director_callback(
            _get_mesh_ctx(), actor_u, c_id, data, cq_id,
            is_owner=is_owner, default_project=def_proj,
            bot_token=TG_BOT_TOKEN, bot_label="main_hq",
            reply_markup_fn=bot_mesh_hub.build_mesh_director_keyboard,
        ):
            return
        parts = data.split(":", 2)
        act = parts[1] if len(parts) > 1 else "status"
        proj = parts[2] if len(parts) > 2 else def_proj
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "⚡ Выполняю..."})
        reply_html = generate_director_reply(actor_u, act, proj)
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": reply_html,
            "parse_mode": "HTML",
            "reply_markup": build_director_inline_keyboard(proj)
        })
    else:
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "OK"})


def _process_bot_reset_command(c_id, from_user, target, new_pwd):
    is_owner = is_owner_chat(c_id) or is_owner_chat((from_user or {}).get("id"))
    sender_username = (from_user or {}).get("username") or c_id
    if len(new_pwd) < 4:
        call_tg("sendMessage", {"chat_id": c_id, "text": "⚠️ Пароль должен содержать минимум 4 символа."})
        return
    reg = fetch_registry(force=True)
    allowed, reason = can_chat_update_user_pwd(reg, c_id, from_user, target)
    if reason == "protected":
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": "🛡️ <b>Доступ защищён:</b> пароль главного аккаунта <code>@unityspiritpartners</code> нельзя обновлять или сбрасывать через <b>@unitybusinessbot</b>.\n<i>Все остальные аккаунты команды открыты для обновления!</i>",
            "parse_mode": "HTML"
        })
        return
    if reason == "not_found":
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": f"⚠️ Аккаунт <code>{_esc_h(target)}</code> не найден. Нажмите /passwords для выбора из списка.",
            "parse_mode": "HTML"
        })
        return
    if allowed:
        ok = set_user_password_in_github(target, new_pwd, verified_chat_id=None if is_owner else c_id)
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": (
                f"🔑 <b>Пароль успешно обновлён через @unitybusinessbot!</b>\n\n"
                f"• Логин: <code>{_esc_h(target)}</code>\n"
                f"• Новый пароль: <code>{_esc_h(new_pwd)}</code>\n"
                f"• Статус: <b>✅ Активен во всех репозиториях</b>"
            ) if ok else f"❌ Не удалось обновить пароль для <code>{_esc_h(target)}</code>. Повторите попытку.",
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[{"text": "🏢 Открыть Офис (v34.0)", "url": OFFICE_URL}]]}
        })
        if ok and not is_owner:
            call_tg("sendMessage", {
                "chat_id": OWNER_TG_CHAT_ID,
                "text": f"🔐 <b>Участник <code>{_esc_h(target)}</code></b> (@{_esc_h(sender_username)}) самостоятельно обновил свой пароль через @unitybusinessbot.",
                "parse_mode": "HTML"
            })
        return

    tg_u = (from_user or {}).get("username") or ""
    ok, code = save_pending_bot_reset_in_github(target, new_pwd, c_id, tg_u)
    if not ok:
        call_tg("sendMessage", {"chat_id": c_id, "text": f"⚠️ Не удалось отправить заявку для <code>{_esc_h(target)}</code>.", "parse_mode": "HTML"})
        return
    call_tg("sendMessage", {
        "chat_id": c_id,
        "text": (
            f"⏳ <b>ЗАЯВКА НА ОБНОВЛЕНИЕ ПАРОЛЯ ОТПРАВЛЕНА В ШТАБ!</b>\n\n"
            f"• Логин: <code>{_esc_h(target)}</code>\n"
            f"• Новый пароль: <code>{_esc_h(new_pwd)}</code>\n"
            f"• Код заявки: <code>{_esc_h(code)}</code>\n\n"
            f"<i>Ожидайте подтверждения от @unityspiritpartners (уведомление придёт сюда автоматически).</i>"
        ),
        "parse_mode": "HTML"
    })
    _notify_owner_bot_pwd_request(target, code, c_id, tg_u)


def send_store_builder_menu(chat_id):
    import store_builder_engine
    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": (
            "🛍️ <b>OPEN V3 AI STORE &amp; TELEGRAM MINI APP БИЛДЕР (#147)</b>\n\n"
            "Выберите нишу ниже для моментальной генерации магазина (бренд + 6 SKU + Shopify CSV + OS 2.0 JSON + живая HTML витрина + рекламный VSL скрипт)\n"
            "либо напишите команду: <code>/store ваша ниша</code>"
        ),
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [
            [{"text": "✨ Бьюти и Пептиды", "callback_data": "store_quick:Пептидный уход и LED beauty"}, {"text": "⚡ Смарт-Гаджеты", "callback_data": "store_quick:Умные MagSafe и GaN гаджеты"}],
            [{"text": "💪 Фитнес & Recovery", "callback_data": "store_quick:Домашний фитнес и массажеры"}, {"text": "🐾 Умные Зоотовары", "callback_data": "store_quick:Умные товары для питомцев"}],
            [{"text": "🌐 Открыть Open V3 Web-Билдер", "url": store_builder_engine.OPEN_V3_WEB_URL}],
            [{"text": "⬅️ Главное меню", "callback_data": "cmd:home"}],
        ]},
    })


def send_revenue_summary_menu(chat_id):
    import store_builder_engine
    rev = store_builder_engine.get_revenue_summary()
    lines = [
        "💰 <b>ВОРОНКА ВЫРУЧКИ И ЛИДОВ ОФИСА (OPEN V3)</b>\n",
        f"• Собрано магазинов / лидов: <b>{rev['total_leads']}</b>",
        f"• Пул Shopify Partner CPA ($150/триал): <b>${rev['shopify_cpa_pool_usd']:,.0f}</b>",
        f"• Потенциал Pro SaaS ($29/мес): <b>${rev['pro_mrr_pool_usd']:,.0f}/мес</b>",
        f"• Прогноз Turnkey DFY ($750, 15%): <b>${rev['projected_dfy_usd']:,.0f}</b>\n",
    ]
    recent = rev.get("recent_leads") or []
    if recent:
        lines.append("<b>Последние сборки:</b>")
        for r in recent[:5]:
            lines.append(f"• <code>{_esc_h(r.get('store_id'))}</code> — <b>{_esc_h(r.get('brand_name'))}</b> ({_esc_h(r.get('niche'))})")
    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": "\n".join(lines),
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [
            [{"text": "🛍️ Собрать новый AI-магазин", "callback_data": "cmd:store_menu"}],
            [{"text": "🌐 Калькулятор и CRM Воронки", "url": store_builder_engine.OPEN_V3_WEB_URL}],
            [{"text": "⬅️ Главное меню", "callback_data": "cmd:home"}],
        ]},
    })


def _execute_store_build_from_tg(chat_id, niche):
    import store_builder_engine
    res = store_builder_engine.build_and_persist_store(
        {"first_name": "Vlaslav", "email": "unityspiritteam@gmail.com", "phone": "@unityspiritpartners", "niche": niche, "language": "ru", "utm_source": "tg_hq_quick"},
        sync_github=True,
        notify_telegram=False,
    )
    sp = res["bundle"]["spec"]
    kit = res["bundle"]["traffic_kit"]
    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": (
            f"✅ <b>Магазин «{_esc_h(sp['brand_name'])}» собран ({_esc_h(sp['store_id'])})!</b>\n"
            f"• ИИ-движок: <code>{_esc_h(str(sp['engine_used']))}</code>\n"
            f"• Товаров: <b>{len(sp['products'])}</b> | Средний чек: <b>${sp['unit_economics']['avg_order_usd']}</b>\n"
            f"• Папка: <code>{_esc_h(res['saved_dir'])}</code>\n\n"
            f"🎬 <b>VSL Хук:</b>\n<code>{_esc_h(kit['vsl_script_45s'][:240])}</code>"
        ),
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [
            [{"text": "🌐 Открыть Web-превью Open V3", "url": store_builder_engine.OPEN_V3_WEB_URL}],
            [{"text": "💰 Статистика лидов", "callback_data": "cmd:revenue"}],
        ]},
    })
    out_dir = res["saved_dir"]
    for fn in ("standalone_storefront.html", "shopify_products.csv"):
        fp = os.path.join(out_dir, fn)
        if os.path.exists(fp):
            call_tg_send_document(chat_id, fp, caption=f"🛍️ {sp['brand_name']} — {fn}")


def _trigger_seo_from_tg(chat_id, arg_text=""):
    import re
    import threading
    import office_seo_worker

    clean = str(arg_text or "").strip()
    low = clean.lower()
    if low in ("status", "статус", "info"):
        call_tg("sendMessage", {
            "chat_id": chat_id,
            "text": office_seo_worker.format_status_for_telegram(),
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [
                [{"text": "🔎 Запустить /seo аудит сейчас", "callback_data": "cmd:seo"}],
                [{"text": "🕸️ Открыть Граф Агентов Live", "url": "https://globalteampartners.github.io/remote-agents-control/agents_live/"}],
                [{"text": "⬅️ Главное меню", "callback_data": "cmd:home"}],
            ]},
        })
        return

    urls = [u.rstrip(".,;:!?)]}»«\"'<>") for u in re.findall(r"https?://[^\s<>'\"«»]+", clean)]
    target_url = urls[0] if urls else ""
    if clean and low not in ("run", "audit", "аудит", "запуск"):
        office_seo_worker.queue_seo_task(clean, url=target_url, priority="high", source="telegram")

    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": (
            f"{office_seo_worker.format_status_for_telegram()}\n\n"
            "⏳ <i>Запускаю полный цикл Claude SEO (47 проверок + Agentic GEO + задачи)... Пришлю свежий отчёт через ~15 сек!</i>"
        ),
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[{"text": "⬅️ Главное меню", "callback_data": "cmd:home"}]]},
    })

    def _worker():
        try:
            only_url = target_url if (target_url and len(clean.split()) <= 2) else ""
            office_seo_worker.run_seo_cycle(trigger="telegram_cmd", force=True, notify=True, single_url=only_url)
        except Exception as _seo_err:
            log_throttled_err("Telegram SEO trigger", _seo_err)

    threading.Thread(target=_worker, daemon=True).start()


def handle_hq_text(msg):
    raw_text = (msg.get("text") or msg.get("caption") or "").strip()
    c_id = str(msg["chat"]["id"])
    from_user = msg.get("from", {})
    sender_username = norm_login(from_user.get("username"))
    is_owner = is_owner_chat(c_id) or is_owner_chat(from_user.get("id"))

    # Strict v34.0 Gate: only @unityspiritpartners or explicitly approved chat_ids may enter @unitybusinessbot
    if not is_owner and not check_bot_entry_gate(c_id, from_user, message_text=raw_text or "[media]"):
        return

    media_rel = ""
    media_desc = ""
    if "photo" in msg or "document" in msg:
        target_proj = "Главный Штаб" if is_owner else "Входящие_Офис"
        media_rel, media_desc = _handle_tg_media_message(
            msg, TG_BOT_TOKEN, sender_username or "user", target_proj, bot_label="main_hq"
        )
        if media_rel and not raw_text:
            director_nlu_engine._persist_bot_work_to_local_and_crm(
                _get_mesh_ctx(), None, target_proj,
                f"Telegram вложение ({media_desc}) от @{sender_username or c_id}",
                f"Файл сохранён в Офис: {media_rel}", urls=[],
            )
            call_tg("sendMessage", {
                "chat_id": c_id,
                "text": f"✅ <b>{media_desc} сохранено в Офис!</b>\n• Папка: <code>{_esc_h(target_proj)}</code>\n• Файл: <code>{_esc_h(media_rel)}</code>\n\nЕсли нужно разобрать содержимое как задачу — перешлите с текстовым комментарием.",
                "parse_mode": "HTML",
                "reply_markup": bot_mesh_hub.build_mesh_director_keyboard(target_proj),
            })
            return

    text = f"{raw_text}\n\n[Вложение сохранено в Офис: {media_rel}]" if (media_rel and raw_text) else raw_text
    if not text:
        return

    # Allow /cancel to exit multi-step input
    if text == "/cancel":
        _chat_states.pop(c_id, None)
        call_tg("sendMessage", {"chat_id": c_id, "text": "❌ Операция отменена."})
        return

    # Check if waiting for interactive text input
    state = _chat_states.get(c_id)
    if state and (time.time() - state.get("ts", 0) < 600) and not text.startswith("/"):
        _chat_states.pop(c_id, None)
        mode = state["mode"]
        target = state["login"]
        if mode == "set_pwd":
            _process_bot_reset_command(c_id, from_user, target, text)
            return
        elif mode == "ext_set_pwd":
            _process_bot_reset_command(c_id, from_user, target, text)
            return
        elif mode == "set_bot_token" and is_owner:
            ok, b_uname = update_user_bot_control(target, "set_token", bot_token=text)
            b_clean = str(b_uname or "").lstrip("@")
            msg_tok = (
                f"✅ Бот <a href=\"https://t.me/{_esc_h(b_clean)}\">@{_esc_h(b_clean)}</a> привязан к <code>@{_esc_h(target)}</code> и сразу запущен!\n\nПередайте ссылку сотруднику: https://t.me/{_esc_h(b_clean)}"
                if ok and b_clean
                else f"⚠️ Токен не прошёл проверку Telegram API (`getMe`) для <code>@{_esc_h(target)}</code>. Проверьте токен из @BotFather."
            )
            call_tg("sendMessage", {
                "chat_id": c_id,
                "text": msg_tok,
                "parse_mode": "HTML"
            })
            return
        elif mode == "set_bot_director" and is_owner:
            ok, _ = update_user_bot_control(target, "set_director", director_name=text)
            call_tg("sendMessage", {
                "chat_id": c_id,
                "text": f"🏷️ Имя Личного Директора для <code>@{_esc_h(target)}</code> изменено на <b>«{_esc_h(text)}»</b>!" if ok else "⚠️ Ошибка",
                "parse_mode": "HTML"
            })
            return
        elif mode == "send_bot_msg" and is_owner:
            reg = fetch_registry(force=True)
            u = find_user_in_registry(reg, target)
            pb = (u or {}).get("personal_bot") or {}
            t_tok = (pb.get("bot_token") or PERSONAL_BOT_TOKENS.get(norm_login(target)) or "").strip()
            if t_tok and pb.get("chat_id"):
                d_n = pb.get("director_name") or "Тим"
                call_tg("sendMessage", {
                    "chat_id": pb["chat_id"],
                    "text": f"👑 <b>Сообщение от Главного Штаба через Директора «{_esc_h(d_n)}»:</b>\n\n{_esc_h(text)}",
                    "parse_mode": "HTML",
                    "reply_markup": build_director_inline_keyboard(f"Личный Офис: @{target}")
                }, bot_token=t_tok)
                record_director_action_and_mirror(u, "hq_directive", f"Личный Офис: @{target}", f"Владелец отправил команду в бот @{target}: {text}", mirror_to_owner=False)
                call_tg("sendMessage", {"chat_id": c_id, "text": f"✅ Сообщение доставлено в личный бот <code>@{_esc_h(target)}</code>.", "parse_mode": "HTML"})
            else:
                call_tg("sendMessage", {"chat_id": c_id, "text": f"⚠️ У <code>@{_esc_h(target)}</code> ещё не настроен Chat ID или Bot Token.", "parse_mode": "HTML"})
            return

    if bot_mesh_hub.handle_hq_mesh_text(_get_mesh_ctx(), msg):
        return

    if text.startswith("/start") or text.startswith("/menu") or text == "/pending":
        send_hq_home_menu(c_id, from_user=from_user)
    elif text.startswith("/botaccess"):
        if not is_owner:
            call_tg("sendMessage", {"chat_id": c_id, "text": "⛔ Управление доступом в бот доступно только @unityspiritpartners."})
            return
        send_bot_access_menu(c_id)
    elif text.startswith("/passwords") or text.startswith("/users"):
        send_passwords_menu(c_id, from_user=from_user)
    elif text.startswith("/antigravity") and is_owner:
        send_antigravity_status_menu(c_id)
    elif text.startswith("/audit") and is_owner:
        send_saved_work_audit_menu(c_id)
    elif text.startswith("/hydrogen"):
        try:
            tel_raw, _ = fetch_json_from_github(GTP_REPO, GTP_TOKEN, "cloud_agents_telemetry.json", branch="data")
            tel = tel_raw if isinstance(tel_raw, dict) else {}
        except Exception:
            tel = {}
        gemini_ok = bool(os.environ.get("GEMINI_API_KEY") or _CFG.get("gemini_api_key"))
        msg_h = (
            "⚛️ <b>ANTIGRAVITY 2.31 ORCHESTRATOR · GEMINI 4 ARGON ↔ CARBON ↔ HYDROGEN (v34.0)</b>\n\n"
            f"• Облачный цикл: <b>#{int(tel.get('cycle_count') or 0)}</b> | Движок: <code>{_esc_h(tel.get('model_engine') or 'structured-cloud-analyzer')}</code>\n"
            f"• Оркестратор: <b>{_esc_h(tel.get('orchestrator_mode') or 'Antigravity 2.31 (Argon <-> Carbon <-> Hydrogen)')}</b>\n"
            f"• Знаний в памяти: <b>{int(tel.get('memory_facts_count') or 0)}</b> | Изучено URL: <b>{int(tel.get('studied_urls_count') or 0)}</b>\n"
            f"• Локальный <code>GEMINI_API_KEY</code>: {'✅ Подключён' if gemini_ok else '⏳ Не задан (работает smart-режим)'}\n"
            "• Архитектура 12–13.11.2026: Argon ↔ Carbon ↔ Hydrogen, контекст 1M, <code>thinking_level</code>: <code>low/medium/high/xhigh</code>, Remote Agents &amp; Live Voice."
        )
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": msg_h,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[{"text": "🏢 Открыть v34.0 (Antigravity 2.31)", "url": OFFICE_URL}]]},
        })
    elif text.startswith("/store"):
        niche_arg = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
        if niche_arg:
            _execute_store_build_from_tg(c_id, niche_arg)
        else:
            send_store_builder_menu(c_id)
    elif text.startswith("/revenue"):
        send_revenue_summary_menu(c_id)
    elif text.startswith("/seo"):
        seo_arg = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
        _trigger_seo_from_tg(c_id, seo_arg)
    elif text.startswith("/bots"):
        if not is_owner:
            call_tg("sendMessage", {"chat_id": c_id, "text": "⛔ Раздел управления ботами команды доступен только Главному Штабу. Для смены своего пароля нажмите /passwords."})
            return
        send_bots_control_menu(c_id)
    elif text.startswith("/set_bot"):
        if not is_owner:
            call_tg("sendMessage", {"chat_id": c_id, "text": "⛔ Привязка токенов ботов доступна только Главному Штабу (@unityspiritpartners)."})
            return
        parts = text.split(None, 2)
        if len(parts) < 3:
            call_tg("sendMessage", {
                "chat_id": c_id,
                "text": "🔑 Формат: <code>/set_bot логин токен_из_BotFather</code>\nПример для Юрия: <code>/set_bot yvyatkin 123456:ABC...</code>\nЛибо откройте /bots и нажмите <b>🔑 Назначить Bot Token</b>.",
                "parse_mode": "HTML"
            })
            return
        t_login, t_tok = parts[1].strip(), parts[2].strip()
        ok, b_uname = update_user_bot_control(t_login, "set_token", bot_token=t_tok)
        b_clean = str(b_uname or "").lstrip("@")
        msg_sb = (
            f"✅ Бот <a href=\"https://t.me/{_esc_h(b_clean)}\">@{_esc_h(b_clean)}</a> привязан к <code>@{_esc_h(t_login)}</code> (и связанному профилю) и активирован!\nСсылка для входа: https://t.me/{_esc_h(b_clean)}"
            if ok and b_clean
            else f"⚠️ Не удалось привязать токен для <code>@{_esc_h(t_login)}</code> (проверьте логин или токен @BotFather)."
        )
        call_tg("sendMessage", {"chat_id": c_id, "text": msg_sb, "parse_mode": "HTML"})
    elif text.startswith("/logs"):
        if not is_owner:
            call_tg("sendMessage", {"chat_id": c_id, "text": "⛔ Журнал Директоров доступен только Главному Штабу. Для смены своего пароля нажмите /passwords."})
            return
        send_directors_log_menu(c_id)
    elif text.startswith("/approve"):
        if not is_owner:
            call_tg("sendMessage", {"chat_id": c_id, "text": "⛔ Команда /approve доступна только Главному Штабу."})
            return
        parts = text.split(None, 1)
        if len(parts) > 1:
            target = parts[1].strip()
            ok = approve_user_in_github(target)
            call_tg("sendMessage", {
                "chat_id": c_id,
                "text": f"✅ Доступ для <code>{_esc_h(target)}</code> одобрен!" if ok else f"⚠️ Пользователь <code>{_esc_h(target)}</code> не найден.",
                "parse_mode": "HTML"
            })
    elif text.startswith("/reset") or text.startswith("/passwd"):
        parts = text.split()
        if len(parts) >= 3:
            _process_bot_reset_command(c_id, from_user, parts[1].strip(), parts[2].strip())
        elif len(parts) == 2 and len(parts[1].strip()) >= 4:
            reg = fetch_registry(force=True)
            matched = find_users_by_tg_sender(reg, from_user, c_id)
            if len(matched) == 1:
                _process_bot_reset_command(c_id, from_user, matched[0]["login"], parts[1].strip())
            else:
                send_passwords_menu(c_id, from_user=from_user)
        else:
            send_passwords_menu(c_id, from_user=from_user)
    else:
        if is_owner:
            actor_u = {"login": sender_username or "unityspiritpartners", "full_name": "Власлав (Главный Штаб)", "personal_bot": {"director_name": "Тим"}}
            director_nlu_engine.handle_director_text(
                _get_mesh_ctx(),
                actor_u,
                c_id,
                text,
                is_owner=True,
                default_project="Главный Штаб",
                bot_token=TG_BOT_TOKEN,
                bot_label="main_hq",
                reply_markup_fn=bot_mesh_hub.build_mesh_director_keyboard,
            )
        else:
            reg = fetch_registry(force=False)
            matched = find_users_by_tg_sender(reg, from_user, c_id)
            if matched:
                u0 = matched[0]
                assg = [p for p in (u0.get("assigned_projects") or []) if p != "*"]
                def_proj = assg[0] if assg else f"Личный Офис: @{u0.get('login')}"
                director_nlu_engine.handle_director_text(
                    _get_mesh_ctx(),
                    u0,
                    c_id,
                    text,
                    is_owner=False,
                    default_project=def_proj,
                    bot_token=TG_BOT_TOKEN,
                    bot_label="main_hq",
                    reply_markup_fn=bot_mesh_hub.build_mesh_director_keyboard,
                )
            else:
                send_member_home_menu(c_id, from_user)


_INSTANCE_LOCK_HANDLE = None


def _acquire_single_instance_lock(port=None):
    """Ensure only one tg_bot_poller.py runs per host (Windows Named Mutex, immune to Hyper-V excluded ports)."""
    global _INSTANCE_LOCK_HANDLE
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            mutex = kernel32.CreateMutexW(None, False, "Local\\GSG_UnityBusinessBot_Poller_V34")
            last_err = kernel32.GetLastError()
            if not mutex:
                return True  # Fail-open only if OS cannot create mutex handle
            if last_err == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(mutex)
                return False
            _INSTANCE_LOCK_HANDLE = mutex
            return True
        except Exception:
            pass
    lock_path = os.path.join(BASE_DIR, ".tg_poller.pid")
    try:
        if os.path.exists(lock_path):
            old_pid = int((open(lock_path, "r", encoding="utf-8").read() or "0").strip() or 0)
            if old_pid > 0 and old_pid != os.getpid():
                try:
                    os.kill(old_pid, 0)
                    return False
                except OSError:
                    pass
        with open(lock_path, "w", encoding="utf-8") as lf:
            lf.write(str(os.getpid()))
        return True
    except Exception:
        return True


def run_loop(single_run=False, skip_startup_seo=False):
    if not single_run and not _acquire_single_instance_lock():
        print("Another tg_bot_poller.py instance is already active (port 47934 locked). Exiting cleanly.", flush=True)
        return
    try:
        sys1_dir = os.path.join(os.path.dirname(BASE_DIR), "System 1")
        if os.path.isdir(sys1_dir) and sys1_dir not in sys.path:
            sys.path.insert(0, sys1_dir)
        import office_money_engine
        if office_money_engine.start_background_money_server(18799):
            print("[MoneyEngine] Local Open V3 & Office HTTP API bound on http://127.0.0.1:18799", flush=True)
    except Exception as _me_err:
        print(f"[MoneyEngine warn] {_me_err}", flush=True)
    if not single_run and not skip_startup_seo:
        try:
            import threading
            import office_seo_worker

            def _boot_seo_first():
                try:
                    office_seo_worker.run_seo_cycle(trigger="poller_startup", force=False, notify=True)
                except Exception as _seo_err:
                    log_throttled_err("Startup SEO runner", _seo_err)

            threading.Thread(target=_boot_seo_first, daemon=True).start()
            print("[SEO-FIRST] Priority Claude SEO startup thread launched first.", flush=True)
        except Exception as _seo_init_err:
            print(f"[SEO-FIRST warn] {_seo_init_err}", flush=True)
    offset = get_persisted_offset()
    print(f"Telegram Multi-Bot Poller Daemon v34.5 (Director NLU + Claude SEO + Antigravity 2.31 + Mesh) started (offset={offset})...", flush=True)
    touch_local_heartbeat_if_due(force=True)
    last_periodic_seo_ts = time.time()

    while True:
        try:
            touch_local_heartbeat_if_due(force=False)
            if not single_run and (time.time() - last_periodic_seo_ts) >= 21600:
                last_periodic_seo_ts = time.time()
                try:
                    import threading
                    import office_seo_worker
                    threading.Thread(
                        target=office_seo_worker.run_seo_cycle,
                        kwargs={"trigger": "periodic_6h", "force": False, "notify": True},
                        daemon=True,
                    ).start()
                except Exception as _pseo_err:
                    log_throttled_err("Periodic SEO check", _pseo_err)
            updates = call_tg("getUpdates", {"offset": offset, "timeout": 4}, retries=1, bot_label="main_hq")
            if updates and updates.get("ok"):
                for item in updates.get("result", []):
                    # Process item BEFORE committing offset if possible, retry cleanly
                    if "callback_query" in item:
                        handle_hq_callback(item["callback_query"])
                    elif "message" in item and any(k in item["message"] for k in ("text", "photo", "document")):
                        handle_hq_text(item["message"])
                    offset = max(offset, item["update_id"] + 1)
                    save_persisted_offset(offset)

            poll_personal_user_bots()
            bot_mesh_hub.sync_outbox_and_cloud_reports(_get_mesh_ctx(), force=False)
        except Exception as e:
            log_throttled_err("Main daemon loop", e)
            time.sleep(2)

        if single_run:
            break


if __name__ == "__main__":
    is_single = "--single" in sys.argv
    skip_seo = "--skip-startup-seo" in sys.argv
    run_loop(single_run=is_single, skip_startup_seo=skip_seo)
