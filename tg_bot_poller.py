# -*- coding: utf-8 -*-
"""
Telegram Multi-Bot Poller Daemon v30.0 for Global Spirit Group ★ Virtual Office.
1. Main HQ Bot (@unitybusinessbot):
   - Reliable 1-click auth approvals (appr:<login>, rst_ok:<login>, rst_no:<login>) with retries & zero dropped clicks.
   - Allows ALL users EXCEPT @unityspiritpartners to update & change their passwords directly in Telegram (/passwords, /reset, interactive buttons).
   - Explicitly protects @unityspiritpartners from any password modification via Telegram bot.
   - Owner Primary Control over ALL users' personal bots (/bots, bot_pick, bot_appr, bot_pause, bot_ban, bot_asktok, bot_askmsg).
   - Live visibility & audit log of every action performed by every user's AI Director (/logs, cmd:dirlog, director_actions_log.json).
2. Personal User Bots:
   - Strictly enforces Owner approval (control_status == "active" required for members).
   - Executes interactive AI Director workflows & mirrors every Director action to @unitybusinessbot and director_actions_log.json.
"""
import sys
import urllib.request
import json
import base64
import datetime
import time
import os
import hashlib
import secrets
import string

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
        "personal_bot_tokens": cfg.get("personal_bot_tokens", {}),
    }

_CFG = _load_config()
TG_BOT_TOKEN = _CFG["tg_bot_token"]
OWNER_TG_CHAT_ID = _CFG["owner_tg_chat_id"]
U_TOKEN = _CFG["u_token"]
GTP_TOKEN = _CFG["gtp_token"]
U_REPO = _CFG["u_repo"]
GTP_REPO = _CFG["gtp_repo"]
PERSONAL_BOT_TOKENS = dict(_CFG.get("personal_bot_tokens") or {})

_cached_registry = None
_cached_registry_ts = 0
_last_net_err_print_ts = 0
_chat_states = {}  # chat_id -> {"mode": ..., "login": ..., "ts": ...}

OFFICE_URL = "https://globalteampartners.github.io/remote-agents-control/?v=31.1"


def _persist_personal_bot_token_local(login_val, bot_token_val):
    nl = norm_login(login_val)
    tok = (bot_token_val or "").strip()
    if not nl:
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
PROTECTED_BOT_PWD_ACCOUNTS = {"unityspiritpartners", "owner", "vlaslav"}


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


def call_tg(method, data=None, bot_token=TG_BOT_TOKEN, retries=2):
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    payload = json.dumps(data or {}).encode("utf-8")
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Connection": "close",
                "User-Agent": "GSG-Bot-Daemon/29.0"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=14) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt + 1 < retries:
                time.sleep(1.0)
            else:
                log_throttled_err(f"TG {method}", e)
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

    return mutate_registry_across_repos(_mutate, f"approve: user {norm_login(login_to_approve)} via @unitybusinessbot [v30.0]")


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

    return mutate_registry_across_repos(_mutate, f"pwd-update: {norm_login(login_target)} via @unitybusinessbot [v30.0]")


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

    ok = mutate_registry_across_repos(_mutate, f"bot-reset-req: {norm_login(login_target)} via @unitybusinessbot [v30.0]")
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
    return mutate_registry_across_repos(_mutate, f"{act}: {norm_login(login_target)} via @unitybusinessbot [v30.0]")


def update_user_bot_control(login_target, action, bot_token=None, director_name=None):
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    now_ts = int(time.time())
    bot_username = ""
    if bot_token and ":" in bot_token:
        me = call_tg("getMe", bot_token=bot_token)
        if me and me.get("ok"):
            bot_username = me["result"].get("username", "")

    def _mutate(reg):
        u = find_user_in_registry(reg, login_target)
        if not u:
            return False
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
            if bot_username:
                pb["bot_username"] = "@" + bot_username
            pb["control_status"] = "active"
            pb["approved_by_owner_at"] = now_iso
        elif action == "set_director" and director_name:
            pb["director_name"] = director_name.strip()
        pb["updated_at"] = now_iso
        u["owner_action_stamp"] = f"bot-{action}-{now_ts}"
        return True

    ok = mutate_registry_across_repos(_mutate, f"bot-ctrl({action}): {norm_login(login_target)} via @unitybusinessbot [v29.0]")
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
        default_factory=lambda: {"version": "29.0", "actions": []}
    )

    if mirror_to_owner and login not in ("unityspiritpartners", "unityspiritpartner", "unityspiritteam@gmail.com"):
        mirror_msg = (
            f"👁️ <b>КОНТРОЛЬ ДИРЕКТОРА СОТРУДНИКА</b>\n\n"
            f"• <b>Пользователь:</b> <code>@{login}</code> ({full_name})\n"
            f"• <b>Бот / Директор:</b> {bot_uname} · «<b>{dir_name}</b>»\n"
            f"• <b>Папка:</b> «{project_name}»\n"
            f"• <b>Действие ({action_type}):</b>\n<i>{str(summary_text)[:450]}</i>\n"
            f"• <b>Время:</b> {now_local}"
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
    p = (project_name or "Мой Офис")[:24]
    kb = [
        [
            {"text": "🚀 Что делаем дальше? (План)", "callback_data": f"pdir:next:{p}"},
            {"text": "⚡ Выполнить шаг #1", "callback_data": f"pdir:exec:{p}"}
        ],
        [
            {"text": "📊 Сводка по моей папке", "callback_data": f"pdir:status:{p}"},
            {"text": "💡 3 идеи для роста дохода", "callback_data": f"pdir:ideas:{p}"}
        ],
        [
            {"text": "🔎 Глубокий ресёрч темы", "callback_data": f"pdir:research:{p}"},
            {"text": "✅ Принять работу и идти дальше", "callback_data": f"pdir:approve:{p}"}
        ],
    ]
    if "alberta" in p.lower() or "aliashirani" in p.lower():
        kb.append([
            {"text": "👑 Мой Бизнес-Хаб & Бренд-Бук", "url": "https://unityspirit.github.io/alberta-hub/"},
            {"text": "🌌 Международный Штаб 5-в-1", "url": "https://unityspirit.github.io/alberta-hub/international/"}
        ])
    kb.append([
        {"text": "🏷️ Сменить имя Директора", "callback_data": "pdir:rename"},
        {"text": "🏢 Открыть Виртуальный Офис", "url": OFFICE_URL}
    ])
    return {"inline_keyboard": kb}


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
            "• <b>KHL AI Predictor Bot (PID 12696):</b> WinRate 82.9%, ROI +53.4%, Brier 0.1716.\n"
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
        ctx_status = (
            f"• <b>Активная папка:</b> «{proj}»\n"
            "• <b>Автономные агенты 24/7:</b> все отделы и 32 проекта синхронизированы.\n"
            "• <b>Топ MVP #1:</b> AltaDesk AI Voice ($750 setup + $350/мес, маржа 94.8%)."
        )
        ctx_next = (
            "1️⃣ Отправить КП AltaDesk AI Voice первым 20 отелям и клиникам.\n"
            f"2️⃣ Проверить свежие отчёты агентов в папке «{proj}».\n"
            "3️⃣ Запустить ресёрч новой ниши через NotebookLM RAG."
        )
        ctx_ideas = (
            "💎 <b>Идея 1:</b> 7-дневный тест ИИ-голосового администратора с переходом на $350/мес.\n"
            "💎 <b>Идея 2:</b> Пакет «3D Кино-Сайт + ИИ-Бот» ($1,500).\n"
            "💎 <b>Идея 3:</b> Авто-поиск бизнесов без виджета связи."
        )

    summary_for_log = f"Команда {action_type} в папке «{proj}»"
    if action_type == "status":
        reply = (
            f"👑 <b>Личный Директор «{dir_name}» — Сводка по папке «{proj}»</b>\n\n"
            f"Здравствуйте, <b>{full_name}</b>! Текущее состояние работ:\n\n"
            f"{ctx_status}\n\n"
            f"💬 <b>Вопрос от «{dir_name}»:</b> <i>Что делаем дальше?</i>"
        )
        summary_for_log = f"Выдана сводка по папке «{proj}»"
    elif action_type == "next":
        reply = (
            f"🚀 <b>Личный Директор «{dir_name}» — План «Что делаем дальше?»</b>\n\n"
            f"Для папки <b>«{proj}»</b> рекомендую 3 приоритетных шага:\n\n"
            f"{ctx_next}\n\n"
            f"💬 <b>Вопрос от «{dir_name}»:</b> <i>Нажмите «⚡ Выполнить шаг #1» или напишите своё задание:</i>"
        )
        summary_for_log = f"Сформирован план из 3 шагов для «{proj}»"
    elif action_type == "exec":
        iss_num = create_github_issue_task(proj, f"DIRECTOR_{dir_name.upper()}: Выполнение шага #1 ({proj})", f"Запущено @{login} через личного директора «{dir_name}».")
        num_str = f" (Задача #{iss_num})" if iss_num else ""
        reply = (
            f"⚡ <b>Личный Директор «{dir_name}» — Задача принята{num_str}!</b>\n\n"
            f"• Папка: <b>«{proj}»</b>\n"
            f"• Статус: <b>🟢 ИИ-агенты выполняют приоритетный шаг #1</b>\n\n"
            f"💬 <b>Вопрос от «{dir_name}»:</b> <i>Подготовить 3 идеи роста дохода или провести ресёрч?</i>"
        )
        summary_for_log = f"Запущен шаг #1 в папке «{proj}»{num_str}"
    elif action_type == "ideas":
        reply = (
            f"💡 <b>Личный Директор «{dir_name}» — 3 Идеи для дохода («{proj}»)</b>\n\n"
            f"{ctx_ideas}\n\n"
            f"💬 <b>Вопрос от «{dir_name}»:</b> <i>Какую идею запускаем? Напишите номер 1, 2 или 3:</i>"
        )
        summary_for_log = f"Предложены 3 идеи монетизации для «{proj}»"
    elif action_type == "research":
        reply = (
            f"🔎 <b>Личный Директор «{dir_name}» — Режим Исследования</b>\n\n"
            f"Пришлите любую <b>тему, вопрос или ссылку</b> — агенты разберут материал и сохранят в папку <b>«{proj}»</b>."
        )
        summary_for_log = f"Открыт режим исследования в «{proj}»"
    elif action_type == "approve":
        reply = (
            f"✅ <b>Личный Директор «{dir_name}» — Этап принят!</b>\n\n"
            f"Отлично, <b>{full_name}</b>! Работа в папке <b>«{proj}»</b> зафиксирована. Переходим к следующей задаче."
        )
        summary_for_log = f"Пользователь принял выполненную работу по «{proj}»"
    else:
        iss_num = create_github_issue_task(proj, f"TIM: [{dir_name}] {custom_text[:120]}", f"Отправлено @{login} через бота (Директор: {dir_name}):\n\n{custom_text}")
        num_str = f" (Задача #{iss_num})" if iss_num else ""
        reply = (
            f"👑 <b>Личный Директор «{dir_name}» принял ваше поручение{num_str}!</b>\n\n"
            f"📥 <b>Текст:</b> <i>«{custom_text}»</i>\n"
            f"📁 <b>Папка:</b> «{proj}»\n\n"
            f"💬 <b>Вопрос от «{dir_name}»:</b> <i>Что делаем дальше параллельно?</i>"
        )
        summary_for_log = f"Поручение от @{login}{num_str}: {custom_text[:200]}"

    record_director_action_and_mirror(user_obj, action_type, proj, summary_for_log)
    return reply


def poll_personal_user_bots():
    reg = fetch_registry(force=False)
    offsets = get_user_bot_offsets()
    offsets_changed = False

    for u in reg.get("users", []):
        pb = u.get("personal_bot")
        if not isinstance(pb, dict):
            continue
        b_tok = (pb.get("bot_token") or "").strip()
        if not b_tok or ":" not in b_tok or b_tok == TG_BOT_TOKEN:
            continue

        u_login = norm_login(u.get("login"))
        ctrl_status = pb.get("control_status") or ("active" if u.get("role") == "owner" else "pending_owner")
        cur_off = int(offsets.get(b_tok, 0))
        updates = call_tg("getUpdates", {"offset": cur_off, "timeout": 1}, bot_token=b_tok, retries=1)
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
                mutate_registry_across_repos(
                    lambda r: any(
                        (x.setdefault("personal_bot", {}).update({"chat_id": c_id}) or True)
                        for x in r.get("users", [])
                        if norm_login(x.get("login")) == u_login
                    ),
                    f"feat(bot): auto-link chat_id {c_id} for {u_login}"
                )

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
                        else f"⏳ <b>Ваш личный бот и Директор «{dir_name}» ожидают подтверждения Главного Штаба (@unityspiritpartners).</b>\n\nЗаявка на активацию уже отправлена Владельцу."
                    )
                    call_tg("sendMessage", {"chat_id": c_id, "text": msg_st, "parse_mode": "HTML"}, bot_token=b_tok)
                    if ctrl_status == "pending_owner":
                        call_tg("sendMessage", {
                            "chat_id": OWNER_TG_CHAT_ID,
                            "text": f"🤖 <b>ПОЛЬЗОВАТЕЛЬ АКТИВИРОВАЛ БОТА (/start) — ЖДЁТ ВАШЕГО КОНТРОЛЯ</b>\n\n• Логин: <code>@{u_login}</code>\n• Директор: <b>«{dir_name}»</b>\n• Бот: {pb.get('bot_username') or 'Telegram Bot'}",
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
                        "text": f"👑 <b>Теперь вашего Личного AI-Директора зовут «{new_name}»!</b>\n\nЧто делаем дальше?",
                        "parse_mode": "HTML",
                        "reply_markup": build_director_inline_keyboard(default_proj)
                    }, bot_token=b_tok)
                elif data.startswith("pdir:"):
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

            elif "message" in item and "text" in item["message"]:
                text = item["message"]["text"].strip()
                if text.startswith("/start"):
                    reply_html = (
                        f"👑 <b>Здравствуйте, {u.get('full_name') or u_login}!</b>\n"
                        f"Я — ваш Личный AI-Директор <b>«{dir_name}»</b> (одобрен Главным Штабом).\n\n"
                        f"📁 Ваша папка: <b>«{default_proj}»</b>\n"
                        f"💬 <i>Выберите команду ниже или напишите любую задачу:</i>"
                    )
                    call_tg("sendMessage", {
                        "chat_id": c_id,
                        "text": reply_html,
                        "parse_mode": "HTML",
                        "reply_markup": build_director_inline_keyboard(default_proj)
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
                            "text": f"✅ Имя Директора изменено на <b>«{new_name}»</b>.",
                            "parse_mode": "HTML",
                            "reply_markup": build_director_inline_keyboard(default_proj)
                        }, bot_token=b_tok)
                else:
                    reply_html = generate_director_reply(u, "custom", default_proj, custom_text=text)
                    call_tg("sendMessage", {
                        "chat_id": c_id,
                        "text": reply_html,
                        "parse_mode": "HTML",
                        "reply_markup": build_director_inline_keyboard(default_proj)
                    }, bot_token=b_tok)

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
    kb.append([{"text": "🏢 Открыть Виртуальный Офис (v30.0)", "url": OFFICE_URL}])

    matched_note = (
        f"• Ваш подтверждённый аккаунт: <code>{', '.join(m.get('login', '') for m in matched)}</code> (прямая смена пароля без ожидания!)\n"
        if matched
        else "• Для мгновенной смены пароля нажмите кнопку ниже или напишите: <code>/reset ваш_логин новый_пароль</code>\n"
    )
    call_tg("sendMessage", {
        "chat_id": chat_id,
        "text": (
            f"👋 <b>Добро пожаловать в @unitybusinessbot v30.0, {fname}!</b>\n\n"
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
            "🔐 <b>СМЕНА ПАРОЛЕЙ КОМАНДЫ (@unitybusinessbot v30.0)</b>\n\n"
            "Выберите любой аккаунт ниже (все пользователи, кроме защищённого <code>@unityspiritpartners</code>), чтобы мгновенно обновить пароль:\n"
            "<i>(Либо командой: <code>/reset логин новый_пароль</code>)</i>"
        )
    else:
        header = (
            "🔐 <b>ОБНОВЛЕНИЕ И СМЕНА ПАРОЛЯ (@unitybusinessbot v30.0)</b>\n\n"
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
        tok = pb.get("bot_token") or ""
        st = pb.get("control_status") or ("active" if tok else "not_configured")
        dir_name = pb.get("director_name") or "Тим"
        b_uname = pb.get("bot_username") or ("подключён" if tok else "не подключён")
        badge = "🟢" if (st == "active" and tok) else ("⏳" if st == "pending_owner" else ("⏸️" if st == "paused" else "⚪"))
        lines.append(f"{badge} <code>@{lg}</code> · Директор: <b>«{dir_name}»</b> · Бот: {b_uname}")
        rows.append([{"text": f"{badge} Управлять ботом @{lg} (Дир: {dir_name})", "callback_data": f"bot_pick:{lg[:45]}"}])
    rows.append([
        {"text": "👁️ Что выполняют их Директора (Лог)", "callback_data": "cmd:dirlog"},
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

    header = f"👁️ <b>ДЕЙСТВИЯ ДИРЕКТОРОВ {('@' + filter_login) if filter_login else 'ВСЕЙ КОМАНДЫ'}</b>\n\n"
    if not actions:
        body = "Пока нет зафиксированных действий Директоров в облачном журнале."
    else:
        chunks = []
        for a in actions[:12]:
            ts = str(a.get("time_local") or a.get("ts", "")[11:19])
            chunks.append(
                f"• <code>{ts}</code> | <b>@{a.get('user_login')}</b> (Дир: «<b>{a.get('director_name')}</b>»)\n"
                f"  📁 {a.get('project')} · <i>{str(a.get('summary',''))[:160]}</i>"
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
    active_bots = [u for u in users if (u.get("personal_bot") or {}).get("bot_token") and (u.get("personal_bot") or {}).get("control_status") == "active"]

    txt = (
        f"👑 <b>ГЛАВНЫЙ БОТ ШТАБА (@unitybusinessbot v30.0)</b>\n\n"
        f"• Сотрудников в реестре: <b>{len(users)}</b>\n"
        f"• Заявок на вход / сброс пароля: <b>{len(pending) + len(resets)}</b>\n"
        f"• Личных ботов активно / ждут одобрения: <b>{len(active_bots)}</b> / <b>{len(pend_bots)}</b>\n"
        f"• 🛡️ Защита: смена пароля через бота разрешена <b>всем, кроме @unityspiritpartners</b>\n\n"
        f"Выберите действие кнопкой ниже:"
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
        {"text": "🔐 Сменить пароль (Все кроме @unityspiritpartners)", "callback_data": "cmd:passwords"},
        {"text": "🤖 Контроль ботов команды", "callback_data": "cmd:bots"}
    ])
    kb.append([
        {"text": "👁️ Что делают их Директора", "callback_data": "cmd:dirlog"},
        {"text": "🚀 План Главного Штаба", "callback_data": "pdir:next:Главный Штаб"}
    ])
    kb.append([
        {"text": "🏢 Открыть Главный Офис (v30.0)", "url": OFFICE_URL}
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


def handle_hq_callback(cq):
    cq_id = cq["id"]
    data = cq.get("data", "")
    from_user = cq.get("from", {})
    user_name = from_user.get("username") or from_user.get("first_name") or "Пользователь"
    c_id = str(cq.get("message", {}).get("chat", {}).get("id") or from_user.get("id") or OWNER_TG_CHAT_ID)
    is_owner = is_owner_chat(c_id) or is_owner_chat(from_user.get("id"))

    admin_prefixes = ("cmd:bots", "cmd:dirlog", "appr:", "rst_ok:", "rst_no:", "bot_pick:", "bot_appr:", "bot_pause:", "bot_ban:", "bot_asktok:", "bot_askdir:", "bot_askmsg:", "bot_log:")
    if any(data == p or data.startswith(p) for p in admin_prefixes) and not is_owner:
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "⛔ Эта функция доступна только Главному Штабу.", "show_alert": True})
        return

    if data == "cmd:home" or data == "cmd:pending":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🔄 Обновляю..."})
        send_hq_home_menu(c_id, from_user=from_user)
    elif data == "cmd:passwords":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🔐 Смена паролей"})
        send_passwords_menu(c_id, from_user=from_user)
    elif data == "cmd:bots":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🤖 Боты команды"})
        send_bots_control_menu(c_id)
    elif data == "cmd:dirlog":
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "👁️ Загружаю журнал..."})
        send_directors_log_menu(c_id)
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
            "reply_markup": {"inline_keyboard": [[{"text": "🏢 Войти в Офис (v30.0)", "url": OFFICE_URL}]]}
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
                "reply_markup": {"inline_keyboard": [[{"text": "🏢 Войти в Офис (v30.0)", "url": OFFICE_URL}]]}
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
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": (
                f"🤖 <b>КОНТРОЛЬ БОТА СОТРУДНИКА <code>@{ul}</code></b>\n\n"
                f"• <b>Статус контроля:</b> <code>{st}</code>\n"
                f"• <b>Имя Директора:</b> «<b>{dir_n}</b>»\n"
                f"• <b>Бот:</b> {pb.get('bot_username') or '—'} (Токен: <code>{tok_mask}</code>)\n"
                f"• <b>Chat ID:</b> <code>{pb.get('chat_id') or 'не привязан'}</code>"
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
                    "text": f"🟢 <b>Главный Штаб (@unityspiritpartners) одобрил и подключил вашего Личного Директора «{d_n}»!</b>\n\nТеперь вы можете управлять задачами прямо отсюда:",
                    "parse_mode": "HTML",
                    "reply_markup": build_director_inline_keyboard(f"Личный Офис: @{ul}")
                }, bot_token=pb["bot_token"])
            call_tg("sendMessage", {"chat_id": c_id, "text": f"🟢 Бот пользователя <code>@{ul}</code> одобрен и запущен в работу.", "parse_mode": "HTML"})
    elif data.startswith("bot_pause:"):
        ul = data.split("bot_pause:", 1)[1]
        ok, _ = update_user_bot_control(ul, "pause")
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"⏸️ Бот @{ul} поставлен на паузу." if ok else "⚠️ Ошибка", "show_alert": True})
        call_tg("sendMessage", {"chat_id": c_id, "text": f"⏸️ Личный бот сотрудника <code>@{ul}</code> поставлен на паузу.", "parse_mode": "HTML"})
    elif data.startswith("bot_ban:"):
        ul = data.split("bot_ban:", 1)[1]
        ok, _ = update_user_bot_control(ul, "disable")
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"🚫 Бот @{ul} отключён." if ok else "⚠️ Ошибка", "show_alert": True})
        call_tg("sendMessage", {"chat_id": c_id, "text": f"🚫 Личный бот сотрудника <code>@{ul}</code> отключён.", "parse_mode": "HTML"})
    elif data.startswith("bot_asktok:"):
        ul = data.split("bot_asktok:", 1)[1]
        _chat_states[c_id] = {"mode": "set_bot_token", "login": ul, "ts": time.time()}
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Отправьте Bot Token для @{ul}"})
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": f"🔑 <b>Отправьте токен бота из @BotFather для сотрудника <code>@{ul}</code></b>:\n<i>Бот автоматически проверится через Telegram API, привяжется к @{ul} и начнёт работу под вашим контролем.</i>",
            "parse_mode": "HTML"
        })
    elif data.startswith("bot_askdir:"):
        ul = data.split("bot_askdir:", 1)[1]
        _chat_states[c_id] = {"mode": "set_bot_director", "login": ul, "ts": time.time()}
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Введите имя Директора для @{ul}"})
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": f"🏷️ <b>Отправьте новое имя Личного Директора для сотрудника <code>@{ul}</code>:</b>",
            "parse_mode": "HTML"
        })
    elif data.startswith("bot_askmsg:"):
        ul = data.split("bot_askmsg:", 1)[1]
        _chat_states[c_id] = {"mode": "send_bot_msg", "login": ul, "ts": time.time()}
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Введите сообщение в бот @{ul}"})
        call_tg("sendMessage", {
            "chat_id": c_id,
            "text": f"💬 <b>Напишите сообщение или задание для отправки прямо в личный бот сотрудника <code>@{ul}</code>:</b>",
            "parse_mode": "HTML"
        })
    elif data.startswith("bot_log:"):
        ul = data.split("bot_log:", 1)[1]
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Лог Директора @{ul}"})
        send_directors_log_menu(c_id, filter_login=ul)
    elif data.startswith("pdir:"):
        parts = data.split(":", 2)
        act = parts[1] if len(parts) > 1 else "status"
        proj = parts[2] if len(parts) > 2 else "Главный Штаб"
        call_tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": "⚡ Выполняю..."})
        if is_owner:
            actor_u = {"login": "unityspiritpartners", "full_name": "Власлав (Главный Штаб)", "personal_bot": {"director_name": "Тим"}}
        else:
            reg_d = fetch_registry(force=False)
            matched_d = find_users_by_tg_sender(reg_d, from_user, c_id)
            actor_u = matched_d[0] if matched_d else {"login": user_name, "full_name": user_name, "personal_bot": {"director_name": "Тим"}}
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
            "reply_markup": {"inline_keyboard": [[{"text": "🏢 Открыть Офис (v30.0)", "url": OFFICE_URL}]]}
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


def handle_hq_text(msg):
    text = msg["text"].strip()
    c_id = str(msg["chat"]["id"])
    from_user = msg.get("from", {})
    sender_username = norm_login(from_user.get("username"))
    is_owner = is_owner_chat(c_id) or is_owner_chat(from_user.get("id"))

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
            call_tg("sendMessage", {
                "chat_id": c_id,
                "text": f"✅ Бот @{_esc_h(b_uname or 'TelegramBot')} привязан к <code>@{_esc_h(target)}</code> и активирован!" if ok else f"⚠️ Не удалось привязать токен для <code>@{_esc_h(target)}</code>.",
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
            if pb.get("bot_token") and pb.get("chat_id"):
                d_n = pb.get("director_name") or "Тим"
                call_tg("sendMessage", {
                    "chat_id": pb["chat_id"],
                    "text": f"👑 <b>Сообщение от Главного Штаба через Директора «{_esc_h(d_n)}»:</b>\n\n{_esc_h(text)}",
                    "parse_mode": "HTML",
                    "reply_markup": build_director_inline_keyboard(f"Личный Офис: @{target}")
                }, bot_token=pb["bot_token"])
                record_director_action_and_mirror(u, "hq_directive", f"Личный Офис: @{target}", f"Владелец отправил команду в бот @{target}: {text}", mirror_to_owner=False)
                call_tg("sendMessage", {"chat_id": c_id, "text": f"✅ Сообщение доставлено в личный бот <code>@{_esc_h(target)}</code>.", "parse_mode": "HTML"})
            else:
                call_tg("sendMessage", {"chat_id": c_id, "text": f"⚠️ У <code>@{_esc_h(target)}</code> ещё не настроен Chat ID или Bot Token.", "parse_mode": "HTML"})
            return

    if text.startswith("/start") or text.startswith("/menu") or text == "/pending":
        send_hq_home_menu(c_id, from_user=from_user)
    elif text.startswith("/passwords") or text.startswith("/users"):
        send_passwords_menu(c_id, from_user=from_user)
    elif text.startswith("/bots"):
        if not is_owner:
            call_tg("sendMessage", {"chat_id": c_id, "text": "⛔ Раздел управления ботами команды доступен только Главному Штабу. Для смены своего пароля нажмите /passwords."})
            return
        send_bots_control_menu(c_id)
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
            reply_html = generate_director_reply(actor_u, "custom", "Главный Штаб", custom_text=text)
            call_tg("sendMessage", {
                "chat_id": c_id,
                "text": reply_html,
                "parse_mode": "HTML",
                "reply_markup": build_director_inline_keyboard("Главный Штаб")
            })
        else:
            reg = fetch_registry(force=False)
            matched = find_users_by_tg_sender(reg, from_user, c_id)
            if matched:
                u0 = matched[0]
                reply_html = generate_director_reply(u0, "custom", f"Личный Офис: @{u0.get('login')}", custom_text=text)
                call_tg("sendMessage", {
                    "chat_id": c_id,
                    "text": reply_html,
                    "parse_mode": "HTML",
                    "reply_markup": build_director_inline_keyboard(f"Личный Офис: @{u0.get('login')}")
                })
            else:
                send_member_home_menu(c_id, from_user)


def run_loop(single_run=False):
    offset = get_persisted_offset()
    print(f"Telegram Multi-Bot Poller Daemon v30.0 started (offset={offset})...", flush=True)

    while True:
        try:
            updates = call_tg("getUpdates", {"offset": offset, "timeout": 4}, retries=1)
            if updates and updates.get("ok"):
                for item in updates.get("result", []):
                    # Process item BEFORE committing offset if possible, retry cleanly
                    if "callback_query" in item:
                        handle_hq_callback(item["callback_query"])
                    elif "message" in item and "text" in item["message"]:
                        handle_hq_text(item["message"])
                    offset = max(offset, item["update_id"] + 1)
                    save_persisted_offset(offset)

            poll_personal_user_bots()
        except Exception as e:
            log_throttled_err("Main daemon loop", e)
            time.sleep(2)

        if single_run:
            break


if __name__ == "__main__":
    is_single = "--single" in sys.argv
    run_loop(single_run=is_single)
