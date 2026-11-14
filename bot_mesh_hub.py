#!/usr/bin/env python3
"""
Global Spirit Group ★ Unified Inter-Bot & Main Office Mesh Hub (v34.2)

Connects all personal Director bots (such as @lermontovplusbot and future member
bots) two-way with:
  1. Main Office live state (crm_data.json, office_memory.json, team_telemetry.json,
     and recent GitHub Issues scoped by strict user project isolation).
  2. Each other (Bot A <-> Bot B) under @unityspiritpartners governance via
     @unitybusinessbot (one-time delivery approval, persistent monitored A<->B
     bridges, HQ inline reply, task escalation, and instant revoke/close).
  3. Automatic delivery of completed 24/7 Cloud Agent reports and Web Office
     Vault outbox messages without storing raw personal bot tokens in public GitHub.
"""

import datetime
import html
import json
import os
import re
import time
import urllib.error
import urllib.request

BRIDGE_FILE = "bot_bridge_registry.json"
WORK_REPORT_FILE = "agent_work_report.json"
MEMORY_FILE = "office_memory.json"
CRM_FILE = "crm_data.json"
TELEMETRY_FILE = "team_telemetry.json"
BOT_ACCESS_FILE = "bot_access_registry.json"

_CACHE = {}
_PENDING_USER_PROMPTS = {}   # f"{bot_uname}:{chat_id}" -> {"mode": ..., "target_login": ..., "expires_at": ts}
_PENDING_OWNER_HQ_REPLY = {} # str(owner_chat_id) -> {"req_id": ..., "expires_at": ts}
_LAST_SYNC_TS = 0.0


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _esc(val):
    return html.escape(str(val or ""), quote=True)


def _norm(ctx, val):
    fn = ctx.get("norm_login")
    return fn(val) if callable(fn) else str(val or "").strip().lower().lstrip("@")


def bridge_pair_key(login_a, login_b):
    a = str(login_a or "").strip().lower().lstrip("@")
    b = str(login_b or "").strip().lower().lstrip("@")
    if not a or not b or a == b:
        return ""
    pair = sorted([a, b])
    return f"{pair[0]}<->{pair[1]}"


def ensure_bridge_shape(obj):
    if not isinstance(obj, dict):
        obj = {}
    bridges = obj.get("bridges")
    if not isinstance(bridges, dict):
        bridges = {}
    requests = obj.get("requests")
    if not isinstance(requests, list):
        requests = []
    delivered = obj.get("delivered_report_ids")
    if not isinstance(delivered, list):
        delivered = []
    return {
        "version": "34.2",
        "updated_at": obj.get("updated_at") or _utc_now(),
        "bridges": bridges,
        "requests": requests[-120:],
        "delivered_report_ids": delivered[-150:],
    }


def _safe_fetch_json(ctx, path, branch="data", default_val=None):
    try:
        fetcher = ctx["fetch_json"]
        val, sha = fetcher(ctx["primary_repo"], ctx["gh_token"], path, branch=branch)
        if val is not None:
            return val, sha
    except Exception:
        pass
    fallback = default_val() if callable(default_val) else default_val
    return fallback, None


def _cached_fetch_json(ctx, path, ttl=25, default_val=None):
    now = time.time()
    cached = _CACHE.get(path)
    if cached and (now - cached["ts"] < ttl):
        return cached["val"]
    val, _ = _safe_fetch_json(ctx, path, branch="data", default_val=default_val)
    _CACHE[path] = {"ts": now, "val": val}
    return val


def _fetch_recent_issues(ctx, limit=8):
    now = time.time()
    cached = _CACHE.get("_recent_issues")
    if cached and (now - cached["ts"] < 25):
        return cached["val"]
    url = f"https://api.github.com/repos/{ctx['primary_repo']}/issues?state=all&sort=updated&direction=desc&per_page={limit}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"token {ctx['gh_token']}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GSG-MeshHub-34.2",
        },
        method="GET",
    )
    items = []
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = json.loads(r.read().decode("utf-8"))
            if isinstance(raw, list):
                items = [x for x in raw if "pull_request" not in x and "[AUTH_SYNC]" not in str(x.get("title") or "")]
    except Exception:
        pass
    _CACHE["_recent_issues"] = {"ts": now, "val": items}
    return items


def load_bridge_registry(ctx):
    obj, _ = _safe_fetch_json(ctx, BRIDGE_FILE, branch="data", default_val=dict)
    return ensure_bridge_shape(obj)


def save_bridge_mutated(ctx, mutator_fn, commit_msg="Update inter-bot bridge registry"):
    result_box = {"obj": None}

    def _wrapped(cur):
        shaped = ensure_bridge_shape(cur)
        updated = mutator_fn(shaped)
        final_obj = ensure_bridge_shape(updated if isinstance(updated, dict) else shaped)
        final_obj["updated_at"] = _utc_now()
        result_box["obj"] = final_obj
        return final_obj

    ctx["mutate_repos"](
        BRIDGE_FILE,
        _wrapped,
        commit_msg,
        branches=["data"],
        default_factory=lambda: ensure_bridge_shape({}),
    )
    _CACHE.pop(BRIDGE_FILE, None)
    return result_box["obj"]


def resolve_bot_route(ctx, reg, target_login):
    lg = _norm(ctx, target_login)
    user_obj = ctx["find_user"](reg, lg) if lg else None
    if not user_obj:
        return {
            "found": False,
            "login": lg,
            "director_name": "Тим",
            "bot_token": "",
            "bot_username": "",
            "chat_id": "",
            "control_status": "not_found",
            "mode": "inbox_queue",
            "owner_chat_collision": False,
        }

    pb = user_obj.get("personal_bot") or {}
    dir_name = pb.get("director_name") or "Тим"
    bot_uname = str(pb.get("bot_username") or "").strip()
    ctrl_status = pb.get("control_status") or ("active" if bot_uname else "not_configured")
    tokens_map = ctx.get("personal_bot_tokens") or {}
    real_tok = (pb.get("bot_token") or "").strip() or (tokens_map.get(lg) or "").strip()
    chat_id = str(pb.get("chat_id") or "").strip()
    owner_chat_id = str(ctx.get("owner_chat_id") or "").strip()
    is_owner_acc = ctx["is_owner_login"](lg)

    collision = bool(chat_id and owner_chat_id and chat_id == owner_chat_id and not is_owner_acc)
    if real_tok and ":" in real_tok and real_tok != ctx["tg_bot_token"] and chat_id and not collision and ctrl_status == "active":
        return {
            "found": True,
            "user": user_obj,
            "login": lg,
            "director_name": dir_name,
            "bot_token": real_tok,
            "bot_username": bot_uname or f"@{lg}_bot",
            "chat_id": chat_id,
            "control_status": ctrl_status,
            "mode": "personal_bot",
            "owner_chat_collision": collision,
        }

    # Fallback: check if this teammate has a verified separate chat_id in bot_access_registry.json
    access_reg = _cached_fetch_json(ctx, BOT_ACCESS_FILE, ttl=30, default_val=dict) or {}
    approved_chats = access_reg.get("approved_chats") or {}
    for cid, entry in approved_chats.items():
        if not isinstance(entry, dict):
            continue
        linked = _norm(ctx, entry.get("linked_login") or entry.get("tg_username") or "")
        if linked == lg and str(cid) != owner_chat_id:
            return {
                "found": True,
                "user": user_obj,
                "login": lg,
                "director_name": dir_name,
                "bot_token": ctx["tg_bot_token"],
                "bot_username": bot_uname or "@unitybusinessbot",
                "chat_id": str(cid),
                "control_status": ctrl_status,
                "mode": "hq_approved_chat",
                "owner_chat_collision": collision,
            }

    return {
        "found": True,
        "user": user_obj,
        "login": lg,
        "director_name": dir_name,
        "bot_token": real_tok,
        "bot_username": bot_uname,
        "chat_id": "" if collision else chat_id,
        "control_status": ctrl_status,
        "mode": "inbox_queue",
        "owner_chat_collision": collision,
    }


def _user_can_see_project(ctx, user_obj, project_name):
    lg = _norm(ctx, (user_obj or {}).get("login") or "")
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


def _extract_project_from_issue(title, body=""):
    m = re.match(r"^\s*\[([^\]]+)\]", str(title or ""))
    if m:
        return m.group(1).strip()
    for pat in (r"Проект:\s*([^\n\r]+)", r"Папка:\s*([^\n\r]+)"):
        bm = re.search(pat, str(body or ""), re.IGNORECASE)
        if bm:
            return bm.group(1).strip()[:60]
    return ""


def build_mesh_director_keyboard(project_name="Мой Офис", extra_row=None):
    p = str(project_name or "Мой Офис")[:24]
    kb = [
        [
            {"text": "⚡ Шаг #1 сейчас", "callback_data": f"pdir:exec:{p}"},
            {"text": "📊 Сводка Офиса", "callback_data": f"pdir:status:{p}"},
        ],
        [
            {"text": "💡 Что дальше?", "callback_data": f"pdir:next:{p}"},
            {"text": "🎯 Главный фокус", "callback_data": f"pdir:priority:{p}"},
        ],
        [
            {"text": "🏛️ В Главный Штаб", "callback_data": "pdir:hqask"},
            {"text": "🤝 Боты и Мосты", "callback_data": "pdir:mesh"},
        ],
        [
            {"text": "📥 Входящие", "callback_data": "pdir:inbox"},
            {"text": "🌐 Веб-Офис v34.2", "url": "https://globalteampartners.github.io/remote-agents-control/?v=34.2"},
        ],
    ]
    if extra_row:
        kb.insert(0, extra_row)
    return {"inline_keyboard": kb}


def build_live_director_reply(ctx, user_obj, action_type="status", project_name="Мой Офис", query_text=""):
    lg = _norm(ctx, (user_obj or {}).get("login") or "user")
    pb = (user_obj or {}).get("personal_bot") or {}
    dir_name = pb.get("director_name") or "Тим"
    bot_uname = pb.get("bot_username") or "Личный бот"

    mem = _cached_fetch_json(ctx, MEMORY_FILE, ttl=25, default_val=dict) or {}
    crm = _cached_fetch_json(ctx, CRM_FILE, ttl=25, default_val=dict) or {}
    tele = _cached_fetch_json(ctx, TELEMETRY_FILE, ttl=25, default_val=dict) or {}
    bridge_reg = load_bridge_registry(ctx)
    recent_issues = _fetch_recent_issues(ctx, limit=10)

    slot = ((tele.get("members") or {}).get(lg) or {}).get("metrics") or {}
    today_req = slot.get("today_requests", 0)
    total_req = slot.get("total_requests", 0)
    kpi = slot.get("kpi_score", 0)

    kb_all = crm.get("knowledge_base") if isinstance(crm.get("knowledge_base"), list) else []
    scoped_kb = [
        item for item in kb_all
        if isinstance(item, dict) and (
            _norm(ctx, item.get("saved_by_login") or "") == lg
            or _user_can_see_project(ctx, user_obj, str(item.get("folder") or item.get("project") or item.get("category") or "").replace("Проект:", "").strip())
        )
    ]
    scoped_issues = []
    for iss in recent_issues:
        ititle = str(iss.get("title") or "")
        ibody = str(iss.get("body") or "")
        proj = _extract_project_from_issue(ititle, ibody)
        if (_norm(ctx, lg) in ibody.lower()) or _user_can_see_project(ctx, user_obj, proj):
            scoped_issues.append(iss)

    pending_inbox = [
        r for r in bridge_reg.get("requests", [])
        if r.get("to_login") == lg and r.get("inbox_unread")
    ]
    active_bridges = [
        k for k, v in bridge_reg.get("bridges", {}).items()
        if isinstance(v, dict) and v.get("status") == "active" and lg in (v.get("pair") or [])
    ]

    cycles = mem.get("cycle_count", 0)
    facts = mem.get("learned_facts") or []
    header = (
        f"👑 <b>Директор «{_esc(dir_name)}» ({_esc(bot_uname)}) · @ {_esc(lg)}</b>\n"
        f"📁 Папка: <b>{_esc(project_name)}</b> | 🌐 Офис: <code>v34.2 Cloud 24/7 (цикл #{cycles})</code>\n"
        f"📈 Ваша активность: сегодня <b>{today_req}</b> | всего <b>{total_req}</b> | KPI: <b>{kpi}</b>\n"
        f"🔗 Активных мостов с ботами: <b>{len(active_bridges)}</b> | 📥 Непрочитанных: <b>{len(pending_inbox)}</b>"
    )

    body_lines = [header, ""]
    if action_type == "hq_ask" and query_text:
        body_lines.append(f"🏛️ <b>Ваш запрос в Главный Штаб зафиксирован:</b>\n<i>{_esc(query_text[:350])}</i>\n")

    if scoped_issues:
        body_lines.append("<b>📌 Последние задачи в вашем контуре:</b>")
        for iss in scoped_issues[:3]:
            st_icon = "✅" if iss.get("state") == "closed" else "🟡"
            body_lines.append(f"{st_icon} <b>#{iss.get('number')}</b> {_esc(str(iss.get('title') or '')[:75])}")
        body_lines.append("")

    if scoped_kb:
        body_lines.append(f"<b>📚 Последние сохранения в Базе Знаний ({len(scoped_kb)} доступно):</b>")
        for item in scoped_kb[-3:][::-1]:
            cat_lbl = item.get('folder') or str(item.get('category') or 'Офис').replace('Проект:', '').strip()
            body_lines.append(f"• [{_esc(cat_lbl)}] {_esc(str(item.get('title') or item.get('source_url') or item.get('url') or '')[:80])}")
        body_lines.append("")

    if facts and action_type in ("next", "ideas", "priority", "hq_ask"):
        last_fact = facts[-1]
        if isinstance(last_fact, dict):
            body_lines.append(f"<b>🧠 Свежий вывод Памяти Офиса:</b>\n<i>{_esc(str(last_fact.get('summary') or last_fact.get('title') or '')[:260])}</i>\n")

    if action_type == "priority":
        body_lines.append("🎯 <b>Рекомендуемый фокус №1 сейчас:</b> закрыть верхнюю открытую задачу, зафиксировать решение и отправить результат в Главный Штаб.")
    elif action_type == "next":
        body_lines.append("🚀 <b>Что делаем дальше?</b>\n1) Нажмите <b>«⚡ Шаг #1 сейчас»</b> — я поставлю задачу облачным агентам 24/7.\n2) Или нажмите <b>«🤝 Боты и Мосты»</b> чтобы связаться с другим ботом/коллегой через <code>@unitybusinessbot</code>.\n3) Или просто напишите мне новый вопрос!")
    else:
        body_lines.append("👇 Выберите действие кнопкой или напишите команду (<code>/hq &lt;вопрос&gt;</code>, <code>/bots</code>, <code>/ask @логин текст</code>):")

    return "\n".join(body_lines)[:3850]


def _send_owner_bridge_card(ctx, req):
    buttons = [
        [
            {"text": "✅ Доставить разово", "callback_data": f"brg_once:{req['id']}"},
            {"text": "🔓 Открыть мост A↔B", "callback_data": f"brg_link:{req['id']}"},
        ],
        [
            {"text": "🏛️ Ответить от Штаба", "callback_data": f"brg_hq:{req['id']}"},
            {"text": "⚡ В ИИ-задачи Офиса", "callback_data": f"brg_exec:{req['id']}"},
        ],
        [
            {"text": "🚫 Отклонить", "callback_data": f"brg_deny:{req['id']}"},
            {"text": "📋 Все мосты", "callback_data": "cmd:bridges"},
        ],
    ]
    kind_title = "🏛️ ЗАПРОС БОТА В ГЛАВНЫЙ ШТАБ" if req.get("kind") == "to_hq" else "🤝 ЗАПРОС НА СВЯЗЬ МЕЖДУ БОТАМИ"
    text = (
        f"<b>{kind_title}</b> (<code>{_esc(req['id'])}</code>)\n\n"
        f"• От: <code>@{_esc(req['from_login'])}</code> · 👑 «{_esc(req['from_director'])}» ({_esc(req['from_bot'])})\n"
        f"• Кому: <b>@{_esc(req['to_login'])}</b> · 👑 «{_esc(req['to_director'])}»\n"
        f"• Папка: <b>{_esc(req.get('project') or 'Офис')}</b>\n"
        f"• Сообщение:\n<blockquote>{_esc(req['text'][:900])}</blockquote>\n"
        f"Выберите решение Главного Штаба:"
    )
    ctx["call_tg"]("sendMessage", {
        "chat_id": ctx["owner_chat_id"],
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": buttons},
    }, bot_token=ctx["tg_bot_token"], bot_label="main_hq")


def deliver_bridge_message_to_target(ctx, req, reg=None):
    if reg is None:
        reg = ctx["fetch_registry"](force=False)
    to_lg = req.get("to_login") or ""
    route = resolve_bot_route(ctx, reg, to_lg)
    is_hq = req.get("kind") in ("hq_to_bot", "hq_reply", "broadcast")
    from_label = "👑 Главный Штаб (@unitybusinessbot)" if is_hq else f"🤝 @{_esc(req['from_login'])} (Директор «{_esc(req['from_director'])}»)"
    msg_html = (
        f"📨 <b>Новое сообщение через Межботовый Мост Офиса</b>\n\n"
        f"• От: {from_label}\n"
        f"• Ваш Директор: <b>«{_esc(route['director_name'])}»</b>\n"
        f"• Папка: <b>{_esc(req.get('project') or 'Офис')}</b>\n\n"
        f"💬 <b>Текст:</b>\n{_esc(req['text'][:1400])}"
    )
    reply_cb = "pdir:hqask" if is_hq else f"pdir:breply:{req['from_login']}"
    reply_label = "🏛️ Ответить в Главный Штаб" if is_hq else f"💬 Ответить @{req['from_login']}"
    kb = {
        "inline_keyboard": [
            [
                {"text": reply_label, "callback_data": reply_cb},
                {"text": "⚡ Запустить в мои ИИ-агенты", "callback_data": f"pdir:bexec:{req['id']}"},
            ],
            [
                {"text": "🤝 Боты и Мосты", "callback_data": "pdir:mesh"},
                {"text": "🏛️ Сводка Офиса", "callback_data": "pdir:status:Мой Офис"},
            ],
        ]
    }
    if route["mode"] in ("personal_bot", "hq_approved_chat") and route["chat_id"] and route["bot_token"]:
        res = ctx["call_tg"]("sendMessage", {
            "chat_id": route["chat_id"],
            "text": msg_html,
            "parse_mode": "HTML",
            "reply_markup": kb,
        }, bot_token=route["bot_token"], bot_label=route["bot_username"])
        if res and res.get("ok"):
            return True, f"delivered:{route['mode']}:{route['bot_username']}", route

    # Queue in inbox if recipient has no configured separate Telegram bot/chat yet
    hint = "Owner chat_id collision detected" if route.get("owner_chat_collision") else "personal bot not connected"
    return False, f"queued_inbox:{hint}", route


def _notify_sender_about_bridge(ctx, req, status_msg, reg=None):
    if reg is None:
        reg = ctx["fetch_registry"](force=False)
    route = resolve_bot_route(ctx, reg, req.get("from_login") or "")
    if route["mode"] in ("personal_bot", "hq_approved_chat") and route["chat_id"] and route["bot_token"]:
        ctx["call_tg"]("sendMessage", {
            "chat_id": route["chat_id"],
            "text": status_msg,
            "parse_mode": "HTML",
            "reply_markup": build_mesh_director_keyboard(req.get("project") or "Мой Офис"),
        }, bot_token=route["bot_token"], bot_label=route["bot_username"])


def initiate_inter_bot_or_hq_request(ctx, user_obj, target_login, text, project_name="Мой Офис", from_source="personal_bot"):
    reg = ctx["fetch_registry"](force=False)
    from_lg = _norm(ctx, user_obj.get("login") or "")
    pb = user_obj.get("personal_bot") or {}
    from_dir = pb.get("director_name") or "Тим"
    from_bot = pb.get("bot_username") or ("@unitybusinessbot" if from_source == "main_bot" else f"@{from_lg}_office")
    clean_text = str(text or "").strip()
    to_lg = "hq" if str(target_login or "").strip().lower() in ("hq", "office", "штаб", "офис") else _norm(ctx, target_login)
    req_id = f"brg_{int(time.time())}_{abs(hash(from_lg + to_lg + clean_text)) % 9000 + 1000}"

    if to_lg == "hq":
        req = {
            "id": req_id,
            "ts": _utc_now(),
            "from_login": from_lg,
            "from_director": from_dir,
            "from_bot": from_bot,
            "to_login": "unityspiritpartners",
            "to_director": "Главный Штаб",
            "kind": "to_hq",
            "project": project_name,
            "text": clean_text[:1500],
            "status": "pending_owner",
            "inbox_unread": False,
        }
        save_bridge_mutated(ctx, lambda obj: (obj["requests"].append(req), obj)[1], f"HQ request {req_id} from {from_lg}")
        _send_owner_bridge_card(ctx, req)
        return req, "sent_to_hq"

    target_user = ctx["find_user"](reg, to_lg)
    to_dir = ((target_user or {}).get("personal_bot") or {}).get("director_name") or "Тим"
    pair_key = bridge_pair_key(from_lg, to_lg)
    req = {
        "id": req_id,
        "ts": _utc_now(),
        "from_login": from_lg,
        "from_director": from_dir,
        "from_bot": from_bot,
        "to_login": to_lg,
        "to_director": to_dir,
        "pair_key": pair_key,
        "kind": "inter_bot",
        "project": project_name,
        "text": clean_text[:1500],
        "status": "pending_owner",
        "inbox_unread": False,
    }

    cur_reg = load_bridge_registry(ctx)
    bridge_entry = (cur_reg.get("bridges") or {}).get(pair_key) or {}
    bridge_active = (bridge_entry.get("status") == "active")

    if bridge_active:
        ok_deliv, via, _ = deliver_bridge_message_to_target(ctx, req, reg=reg)
        req["status"] = "delivered_via_bridge" if ok_deliv else "queued_inbox_via_bridge"
        req["delivered_via"] = via
        req["delivered_at"] = _utc_now()
        req["inbox_unread"] = not ok_deliv

        def _apply_bridge(obj):
            if pair_key in obj["bridges"]:
                obj["bridges"][pair_key]["message_count"] = int(obj["bridges"][pair_key].get("message_count") or 0) + 1
                obj["bridges"][pair_key]["last_msg_at"] = _utc_now()
            obj["requests"].append(req)
            return obj

        save_bridge_mutated(ctx, _apply_bridge, f"Bridge relay {from_lg}->{to_lg}")
        audit_text = (
            f"📡 <b>Мост A↔B (под вашим контролем)</b>\n"
            f"<code>@{_esc(from_lg)}</code> («{_esc(from_dir)}») ➜ <b>@{_esc(to_lg)}</b> («{_esc(to_dir)}»)\n"
            f"Доставка: <code>{_esc(via)}</code>\n"
            f"<blockquote>{_esc(clean_text[:600])}</blockquote>"
        )
        ctx["call_tg"]("sendMessage", {
            "chat_id": ctx["owner_chat_id"],
            "text": audit_text,
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[{"text": f"🛑 Закрыть мост {pair_key}", "callback_data": f"brg_close:{pair_key}"}]]},
        }, bot_token=ctx["tg_bot_token"], bot_label="main_hq")
        return req, ("relayed_via_bridge" if ok_deliv else "queued_inbox_via_bridge")

    save_bridge_mutated(ctx, lambda obj: (obj["requests"].append(req), obj)[1], f"Pending bridge request {req_id}")
    _send_owner_bridge_card(ctx, req)
    return req, "pending_owner"


def _send_mesh_directory_to_bot(ctx, bot_tok, bot_uname, chat_id, user_obj, reg):
    lg = _norm(ctx, user_obj.get("login") or "")
    bridge_reg = load_bridge_registry(ctx)
    bridges = bridge_reg.get("bridges") or {}

    lines = [
        f"🤝 <b>Сеть ботов Офиса и связь с Главным Штабом (@unitybusinessbot)</b>",
        f"Вы: <code>@{_esc(lg)}</code> · Директор «{_esc((user_obj.get('personal_bot') or {}).get('director_name') or 'Тим')}»\n",
    ]
    kb_rows = [[{"text": "🏛️ Написать в Главный Штаб (@unitybusinessbot)", "callback_data": "pdir:hqask"}]]
    row = []
    for u in (reg.get("users") or []):
        ulg = _norm(ctx, u.get("login") or "")
        if not ulg or ulg == lg:
            continue
        route = resolve_bot_route(ctx, reg, ulg)
        pk = bridge_pair_key(lg, ulg)
        b_active = isinstance(bridges.get(pk), dict) and bridges[pk].get("status") == "active"
        b_icon = "🟢" if route["mode"] == "personal_bot" else ("🔵" if route["mode"] == "hq_approved_chat" else "⚪")
        m_badge = "🔓 Мост открыт" if b_active else "🔒 Запрос через Штаб"
        bname = route["bot_username"] or "входящие Офиса"
        lines.append(f"{b_icon} <b>@{_esc(ulg)}</b> · «{_esc(route['director_name'])}» ({_esc(bname)}) — {m_badge}")
        row.append({"text": f"💬 @{ulg[:14]}", "callback_data": f"pdir:ask:{ulg}"})
        if len(row) == 2:
            kb_rows.append(row)
            row = []
    if row:
        kb_rows.append(row)
    kb_rows.append([
        {"text": "📥 Мои входящие", "callback_data": "pdir:inbox"},
        {"text": "📊 Сводка Офиса", "callback_data": "pdir:status:Мой Офис"},
    ])
    ctx["call_tg"]("sendMessage", {
        "chat_id": chat_id,
        "text": "\n".join(lines)[:3800],
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": kb_rows},
    }, bot_token=bot_tok, bot_label=bot_uname)


def handle_personal_bot_mesh_interaction(ctx, item, user_obj, bot_tok, bot_uname, reg):
    """Handle v34.2 mesh callbacks & commands in a personal bot. Returns True if handled."""
    lg = _norm(ctx, user_obj.get("login") or "")
    pb = user_obj.get("personal_bot") or {}
    dir_name = pb.get("director_name") or "Тим"
    projs = [p for p in (user_obj.get("assigned_projects") or []) if p != "*"]
    proj = projs[0] if projs else "Мой Офис"

    cq = item.get("callback_query")
    if cq:
        data = str(cq.get("data") or "")
        chat_id = str((cq.get("message") or {}).get("chat", {}).get("id") or pb.get("chat_id") or "")
        cq_id = cq.get("id")

        if data == "pdir:mesh":
            ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🤝 Загружаю сеть ботов..."}, bot_token=bot_tok, bot_label=bot_uname)
            _send_mesh_directory_to_bot(ctx, bot_tok, bot_uname, chat_id, user_obj, reg)
            return True

        if data == "pdir:hqask":
            _PENDING_USER_PROMPTS[f"{bot_uname}:{chat_id}"] = {"mode": "to_hq", "target_login": "hq", "expires_at": time.time() + 300}
            ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🏛️ Жду ваш вопрос для Штаба"}, bot_token=bot_tok, bot_label=bot_uname)
            ctx["call_tg"]("sendMessage", {
                "chat_id": chat_id,
                "text": "🏛️ <b>Напишите ваш запрос, отчёт или задачу для Главного Штаба (@unitybusinessbot):</b>\nСледующее ваше сообщение уйдёт напрямую Владельцу и в ИИ-Штаб Офиса.",
                "parse_mode": "HTML",
            }, bot_token=bot_tok, bot_label=bot_uname)
            return True

        if data.startswith("pdir:ask:") or data.startswith("pdir:breply:"):
            target_lg = data.split(":", 2)[2].strip()
            _PENDING_USER_PROMPTS[f"{bot_uname}:{chat_id}"] = {"mode": "ask_bot", "target_login": target_lg, "expires_at": time.time() + 300}
            ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"💬 Напишите сообщение @{target_lg}"}, bot_token=bot_tok, bot_label=bot_uname)
            ctx["call_tg"]("sendMessage", {
                "chat_id": chat_id,
                "text": f"💬 <b>Напишите сообщение для бота @ {_esc(target_lg)}:</b>\nОтправьте текст следующим сообщением — оно пойдёт через контроль @unitybusinessbot.",
                "parse_mode": "HTML",
            }, bot_token=bot_tok, bot_label=bot_uname)
            return True

        if data == "pdir:inbox":
            ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": "📥 Читаю входящие..."}, bot_token=bot_tok, bot_label=bot_uname)
            bridge_reg = load_bridge_registry(ctx)
            incoming = [r for r in bridge_reg.get("requests", []) if r.get("to_login") == lg][-8:][::-1]
            if not incoming:
                txt = "📥 <b>Входящих сообщений от других ботов и Штаба пока нет.</b>"
            else:
                lines = [f"📥 <b>Последние входящие сообщения для @{_esc(lg)}:</b>\n"]
                for r in incoming[:6]:
                    lines.append(f"• <b>от @{_esc(r.get('from_login'))}</b> [{_esc(r.get('status'))}]: {_esc(str(r.get('text') or '')[:160])}")
                txt = "\n".join(lines)
                save_bridge_mutated(ctx, lambda obj: (
                    [x.__setitem__("inbox_unread", False) for x in obj["requests"] if x.get("to_login") == lg],
                    obj
                )[1], f"Mark inbox read for {lg}")
            ctx["call_tg"]("sendMessage", {
                "chat_id": chat_id,
                "text": txt,
                "parse_mode": "HTML",
                "reply_markup": build_mesh_director_keyboard(proj),
            }, bot_token=bot_tok, bot_label=bot_uname)
            return True

        if data.startswith("pdir:bexec:"):
            req_id = data.split("pdir:bexec:", 1)[1].strip()
            bridge_reg = load_bridge_registry(ctx)
            req_obj = next((r for r in bridge_reg.get("requests", []) if r.get("id") == req_id), None)
            if not req_obj:
                ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": "Запрос не найден"}, bot_token=bot_tok, bot_label=bot_uname)
                return True
            ok, iss_num, iss_url = ctx["create_issue"](
                proj,
                f"Межботовая задача для Директора {dir_name}: {req_obj['text'][:70]}",
                f"Источник: {req_id} от @{req_obj['from_login']}\nИсполнитель в офисе: @{lg}\n\n{req_obj['text']}",
            )
            ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"⚡ Задача #{iss_num} создана!" if ok else "⚠️ Ошибка создания задачи"}, bot_token=bot_tok, bot_label=bot_uname)
            if ok:
                ctx["record_action"](user_obj, "mesh_exec", proj, f"Создана облачная задача #{iss_num} из сообщения @{req_obj['from_login']}", source="telegram_bot", mirror_to_owner=True)
                ctx["call_tg"]("sendMessage", {
                    "chat_id": chat_id,
                    "text": f"⚡ <b>Задача #{iss_num} запущена в ИИ-агентах Офиса 24/7!</b>\n<a href=\"{_esc(iss_url)}\">Открыть задачу #{iss_num}</a>",
                    "parse_mode": "HTML",
                    "reply_markup": build_mesh_director_keyboard(proj),
                }, bot_token=bot_tok, bot_label=bot_uname)
            return True

        if data.startswith("pdir:") and not data.startswith("pdir:setname:") and not data.startswith("pdir:exec:"):
            parts = data.split(":", 2)
            act = parts[1] if len(parts) > 1 else "status"
            p_arg = parts[2] if len(parts) > 2 else proj
            ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"👑 Директор «{dir_name}» готовит ответ..."}, bot_token=bot_tok, bot_label=bot_uname)
            reply = build_live_director_reply(ctx, user_obj, action_type=act, project_name=p_arg)
            ctx["call_tg"]("sendMessage", {
                "chat_id": chat_id,
                "text": reply,
                "parse_mode": "HTML",
                "reply_markup": build_mesh_director_keyboard(p_arg),
            }, bot_token=bot_tok, bot_label=bot_uname)
            ctx["record_action"](user_obj, act, p_arg, f"Интерактив в боте ({act})", source="telegram_bot", mirror_to_owner=True)
            return True

        return False

    msg = item.get("message") or {}
    txt = str(msg.get("text") or "").strip()
    chat_id = str((msg.get("chat") or {}).get("id") or pb.get("chat_id") or "")
    if not txt or not chat_id:
        return False

    # Check active 1-step prompt waiting for reply text
    p_key = f"{bot_uname}:{chat_id}"
    pending = _PENDING_USER_PROMPTS.get(p_key)
    if pending and time.time() > float(pending.get("expires_at") or 0):
        _PENDING_USER_PROMPTS.pop(p_key, None)
        pending = None

    if pending and not txt.startswith("/"):
        _PENDING_USER_PROMPTS.pop(p_key, None)
        t_lg = pending.get("target_login") or "hq"
        req, state = initiate_inter_bot_or_hq_request(ctx, user_obj, t_lg, txt, project_name=proj)
        if state == "sent_to_hq":
            live_ans = build_live_director_reply(ctx, user_obj, action_type="hq_ask", project_name=proj, query_text=txt)
            ctx["call_tg"]("sendMessage", {
                "chat_id": chat_id,
                "text": f"✅ <b>Отправлено в Главный Штаб (@unitybusinessbot)!</b>\n\n{live_ans}",
                "parse_mode": "HTML",
                "reply_markup": build_mesh_director_keyboard(proj),
            }, bot_token=bot_tok, bot_label=bot_uname)
        elif state == "relayed_via_bridge":
            ctx["call_tg"]("sendMessage", {"chat_id": chat_id, "text": f"⚡ <b>Доставлено боту @{_esc(t_lg)}</b> по одобренному мосту! Копия передана в @unitybusinessbot.", "parse_mode": "HTML"}, bot_token=bot_tok, bot_label=bot_uname)
        else:
            ctx["call_tg"]("sendMessage", {"chat_id": chat_id, "text": f"⏳ Запрос для <b>@{_esc(t_lg)}</b> передан Владельцу @unityspiritpartners в @unitybusinessbot на подтверждение.", "parse_mode": "HTML"}, bot_token=bot_tok, bot_label=bot_uname)
        ctx["record_action"](user_obj, "mesh_send", proj, f"Запрос к {t_lg}: {txt[:120]}", source="telegram_bot", mirror_to_owner=True)
        return True

    low = txt.lower()
    if low in ("/bots", "/mesh"):
        _send_mesh_directory_to_bot(ctx, bot_tok, bot_uname, chat_id, user_obj, reg)
        return True

    if low in ("/office", "/hq") or low.startswith("/hq ") or low.startswith("/office "):
        q = txt.split(" ", 1)[1].strip() if " " in txt else ""
        if q:
            initiate_inter_bot_or_hq_request(ctx, user_obj, "hq", q, project_name=proj)
        reply = build_live_director_reply(ctx, user_obj, action_type="hq_ask" if q else "status", project_name=proj, query_text=q)
        ctx["call_tg"]("sendMessage", {
            "chat_id": chat_id,
            "text": reply,
            "parse_mode": "HTML",
            "reply_markup": build_mesh_director_keyboard(proj),
        }, bot_token=bot_tok, bot_label=bot_uname)
        return True

    if low.startswith("/ask "):
        parts = txt.split(" ", 2)
        if len(parts) < 3:
            ctx["call_tg"]("sendMessage", {"chat_id": chat_id, "text": "Формат: <code>/ask @логин ваш вопрос</code>\nИли нажмите «🤝 Боты и Мосты».", "parse_mode": "HTML", "reply_markup": build_mesh_director_keyboard(proj)}, bot_token=bot_tok, bot_label=bot_uname)
            return True
        target_lg = parts[1].strip()
        msg_body = parts[2].strip()
        req, state = initiate_inter_bot_or_hq_request(ctx, user_obj, target_lg, msg_body, project_name=proj)
        note = "✅ Доставлено по активному мосту!" if state == "relayed_via_bridge" else ("✅ Передано в Главный Штаб!" if state == "sent_to_hq" else "⏳ Отправлено в @unitybusinessbot на подтверждение Владельцу.")
        ctx["call_tg"]("sendMessage", {"chat_id": chat_id, "text": f"{note} (<code>{_esc(req['id'])}</code>)", "parse_mode": "HTML", "reply_markup": build_mesh_director_keyboard(proj)}, bot_token=bot_tok, bot_label=bot_uname)
        return True

    return False


def send_owner_bridges_dashboard(ctx, chat_id):
    reg = ctx["fetch_registry"](force=False)
    bridge_reg = load_bridge_registry(ctx)
    bridges = bridge_reg.get("bridges") or {}
    reqs = bridge_reg.get("requests") or []
    pending = [r for r in reqs if r.get("status") == "pending_owner"]

    lines = [
        f"🏛️ <b>ПУЛЬТ УПРАВЛЕНИЯ БОТАМИ И МЕЖБОТОВЫМИ МОСТАМИ (v34.2)</b>\n",
        "<b>🤖 Боты и участники Офиса:</b>",
    ]
    for u in (reg.get("users") or []):
        ulg = _norm(ctx, u.get("login") or "")
        route = resolve_bot_route(ctx, reg, ulg)
        icon = "🟢" if route["mode"] == "personal_bot" else ("🔵" if route["mode"] == "hq_approved_chat" else "⚪")
        extra = " ⚠️ chat_id совпадает с Владельцем" if route.get("owner_chat_collision") else ""
        lines.append(f"{icon} <code>@{_esc(ulg)}</code> · «{_esc(route['director_name'])}» ({_esc(route['bot_username'] or route['mode'])}{extra})")

    lines.append(f"\n<b>🔓 Активные прямые мосты (A↔B): {sum(1 for b in bridges.values() if isinstance(b, dict) and b.get('status') == 'active')}</b>")
    kb = []
    for pk, b in bridges.items():
        if isinstance(b, dict) and b.get("status") == "active":
            lines.append(f"• <code>{_esc(pk)}</code> (сообщений: {b.get('message_count', 0)})")
            kb.append([{"text": f"🛑 Закрыть мост {pk}", "callback_data": f"brg_close:{pk}"}])

    lines.append(f"\n<b>⏳ Ожидают решения Штаба: {len(pending)}</b>")
    for r in pending[-4:]:
        lines.append(f"• <code>{_esc(r['id'])}</code>: @{_esc(r['from_login'])} ➜ @{_esc(r['to_login'])}: <i>{_esc(str(r.get('text') or '')[:80])}</i>")
        kb.append([
            {"text": f"✅ Разово ({r['from_login']}→{r['to_login']})", "callback_data": f"brg_once:{r['id']}"},
            {"text": f"🔓 Мост A↔B", "callback_data": f"brg_link:{r['id']}"},
            {"text": "🚫", "callback_data": f"brg_deny:{r['id']}"},
        ])

    lines.append("\n💡 Команда Штаба всем активным ботам: <code>/broadcast текст объявления</code>")
    kb.append([{"text": "🏠 Главное меню Офиса", "callback_data": "cmd:home"}])

    ctx["call_tg"]("sendMessage", {
        "chat_id": chat_id,
        "text": "\n".join(lines)[:3900],
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": kb},
    }, bot_token=ctx["tg_bot_token"], bot_label="main_hq")


def handle_hq_mesh_callback(ctx, cq):
    """Handle cmd:bridges and brg_* callbacks in @unitybusinessbot. Returns True if handled."""
    data = str(cq.get("data") or "")
    if not (data == "cmd:bridges" or data.startswith("brg_")):
        return False

    cq_id = cq["id"]
    c_id = str((cq.get("message") or {}).get("chat", {}).get("id") or ctx["owner_chat_id"])
    from_user = cq.get("from", {})
    if not (ctx["is_owner_chat"](c_id) or ctx["is_owner_chat"](from_user.get("id"))):
        ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🚫 Только для @unityspiritpartners", "show_alert": True}, bot_token=ctx["tg_bot_token"], bot_label="main_hq")
        return True

    if data == "cmd:bridges":
        ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🤝 Загружаю мосты..."}, bot_token=ctx["tg_bot_token"], bot_label="main_hq")
        send_owner_bridges_dashboard(ctx, c_id)
        return True

    reg = ctx["fetch_registry"](force=False)

    if data.startswith("brg_close:"):
        pk = data.split("brg_close:", 1)[1].strip()
        def _close(obj):
            if pk in obj["bridges"]:
                obj["bridges"][pk]["status"] = "closed"
                obj["bridges"][pk]["closed_at"] = _utc_now()
            return obj
        save_bridge_mutated(ctx, _close, f"Owner close bridge {pk}")
        ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"🛑 Мост {pk} закрыт"}, bot_token=ctx["tg_bot_token"], bot_label="main_hq")
        send_owner_bridges_dashboard(ctx, c_id)
        return True

    req_id = data.split(":", 1)[1].strip() if ":" in data else ""
    bridge_reg = load_bridge_registry(ctx)
    req_obj = next((r for r in bridge_reg.get("requests", []) if r.get("id") == req_id), None)
    if not req_obj:
        ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": "Запрос не найден"}, bot_token=ctx["tg_bot_token"], bot_label="main_hq")
        return True

    if data.startswith("brg_hq:"):
        _PENDING_OWNER_HQ_REPLY[str(c_id)] = {"req_id": req_id, "expires_at": time.time() + 300}
        ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🏛️ Введите ответ для бота..."}, bot_token=ctx["tg_bot_token"], bot_label="main_hq")
        ctx["call_tg"]("sendMessage", {
            "chat_id": c_id,
            "text": f"🏛️ <b>Напишите официальный ответ Штаба для @{_esc(req_obj['from_login'])}</b> (на запрос <code>{_esc(req_id)}</code>):",
            "parse_mode": "HTML",
        }, bot_token=ctx["tg_bot_token"], bot_label="main_hq")
        return True

    if data.startswith("brg_exec:"):
        ok, iss_num, iss_url = ctx["create_issue"](
            req_obj.get("project") or "Входящие_Офис",
            f"[Штаб/Бот] @{req_obj['from_login']}: {req_obj['text'][:65]}",
            f"Запрос из бота @{req_obj['from_login']} ➜ @{req_obj['to_login']}\nID: {req_id}\n\n{req_obj['text']}",
        )
        ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"⚡ Задача #{iss_num} создана" if ok else "⚠️ Ошибка"}, bot_token=ctx["tg_bot_token"], bot_label="main_hq")
        if ok:
            _notify_sender_about_bridge(ctx, req_obj, f"⚡ Главный Штаб запустил ваш запрос в Cloud Agents (#{_esc(iss_num)}).", reg=reg)
        return True

    if data.startswith("brg_deny:"):
        save_bridge_mutated(ctx, lambda obj: ([r.update({"status": "denied", "decided_at": _utc_now()}) for r in obj["requests"] if r.get("id") == req_id], obj)[1], f"Owner deny bridge {req_id}")
        ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": "🚫 Запрос отклонён"}, bot_token=ctx["tg_bot_token"], bot_label="main_hq")
        _notify_sender_about_bridge(ctx, req_obj, f"🚫 Главный Штаб отклонил передачу запроса <code>{_esc(req_id)}</code> к @{_esc(req_obj['to_login'])}.", reg=reg)
        return True

    if data.startswith("brg_once:") or data.startswith("brg_link:"):
        is_link = data.startswith("brg_link:")
        ok_deliv, via, _ = deliver_bridge_message_to_target(ctx, req_obj, reg=reg)
        pk = req_obj.get("pair_key") or bridge_pair_key(req_obj["from_login"], req_obj["to_login"])

        def _approve(obj):
            if is_link and pk:
                obj["bridges"][pk] = {
                    "pair": sorted([req_obj["from_login"], req_obj["to_login"]]),
                    "status": "active",
                    "approved_by": "@unityspiritpartners",
                    "approved_at": _utc_now(),
                    "message_count": int((obj["bridges"].get(pk) or {}).get("message_count") or 0) + 1,
                }
            for r in obj["requests"]:
                if r.get("id") == req_id:
                    r["status"] = "delivered_via_bridge" if (is_link and ok_deliv) else ("delivered_once" if ok_deliv else "queued_inbox")
                    r["delivered_via"] = via
                    r["delivered_at"] = _utc_now()
                    r["inbox_unread"] = not ok_deliv
            return obj

        save_bridge_mutated(ctx, _approve, f"Owner approve {req_id}")
        ctx["call_tg"]("answerCallbackQuery", {"callback_query_id": cq_id, "text": "✅ Доставлено" if ok_deliv else "📥 Входящие @target"}, bot_token=ctx["tg_bot_token"], bot_label="main_hq")
        mode_note = "🔓 Мост A↔B открыт!" if is_link else "✅ Разовая доставка выполнена!"
        deliv_note = f"получатель: @{_esc(req_obj['to_login'])} (<code>{_esc(via)}</code>)"
        _notify_sender_about_bridge(ctx, req_obj, f"{mode_note} Сообщение <code>{_esc(req_id)}</code> передано ({deliv_note}).", reg=reg)
        ctx["call_tg"]("sendMessage", {
            "chat_id": c_id,
            "text": f"{mode_note}\nОт: @{_esc(req_obj['from_login'])} ➜ @{_esc(req_obj['to_login'])}\nСтатус доставки: <code>{_esc(via)}</code>",
            "parse_mode": "HTML",
        }, bot_token=ctx["tg_bot_token"], bot_label="main_hq")
        return True

    return False


def handle_hq_mesh_text(ctx, msg):
    """Handle /bridges, /broadcast and pending HQ replies in @unitybusinessbot."""
    txt = str(msg.get("text") or "").strip()
    c_id = str((msg.get("chat") or {}).get("id") or "")
    from_user = msg.get("from", {})
    if not (ctx["is_owner_chat"](c_id) or ctx["is_owner_chat"](from_user.get("id"))):
        return False

    low = txt.lower()
    if low == "/bridges":
        send_owner_bridges_dashboard(ctx, c_id)
        return True

    if low.startswith("/broadcast "):
        body_msg = txt.split(" ", 1)[1].strip()
        if not body_msg:
            return True
        reg = ctx["fetch_registry"](force=False)
        sent = 0
        for u in (reg.get("users") or []):
            ulg = _norm(ctx, u.get("login") or "")
            if not ulg:
                continue
            route = resolve_bot_route(ctx, reg, ulg)
            if route["mode"] in ("personal_bot", "hq_approved_chat") and route["chat_id"] and route["bot_token"]:
                req_b = {
                    "id": f"brd_{int(time.time())}_{ulg[:6]}",
                    "from_login": "unityspiritpartners",
                    "from_director": "Главный Штаб",
                    "to_login": ulg,
                    "kind": "broadcast",
                    "project": "Главный Штаб",
                    "text": body_msg,
                }
                ok_d, _, _ = deliver_bridge_message_to_target(ctx, req_b, reg=reg)
                if ok_d:
                    sent += 1
        ctx["call_tg"]("sendMessage", {"chat_id": c_id, "text": f"📢 Рассылка из Главного Штаба доставлена в <b>{sent}</b> активных ботов!", "parse_mode": "HTML"}, bot_token=ctx["tg_bot_token"], bot_label="main_hq")
        return True

    pending = _PENDING_OWNER_HQ_REPLY.get(str(c_id))
    if pending and time.time() > float(pending.get("expires_at") or 0):
        _PENDING_OWNER_HQ_REPLY.pop(str(c_id), None)
        pending = None

    if pending and not txt.startswith("/"):
        _PENDING_OWNER_HQ_REPLY.pop(str(c_id), None)
        req_id = pending.get("req_id")
        reg = ctx["fetch_registry"](force=False)
        bridge_reg = load_bridge_registry(ctx)
        req_obj = next((r for r in bridge_reg.get("requests", []) if r.get("id") == req_id), None)
        if req_obj:
            reply_req = {
                "id": f"hq_{int(time.time())}",
                "from_login": "unityspiritpartners",
                "from_director": "Главный Штаб",
                "to_login": req_obj["from_login"],
                "kind": "hq_reply",
                "project": req_obj.get("project") or "Офис",
                "text": txt,
            }
            ok_d, via, _ = deliver_bridge_message_to_target(ctx, reply_req, reg=reg)
            save_bridge_mutated(ctx, lambda obj: ([r.update({"status": "hq_replied", "reply_text": txt[:800], "replied_at": _utc_now()}) for r in obj["requests"] if r.get("id") == req_id], obj)[1], f"HQ reply {req_id}")
            ctx["call_tg"]("sendMessage", {"chat_id": c_id, "text": f"🏛️ Ответ Штаба для @{_esc(req_obj['from_login'])}: <code>{_esc(via if ok_d else 'сохранено во входящих')}</code>", "parse_mode": "HTML"}, bot_token=ctx["tg_bot_token"], bot_label="main_hq")
            return True

    return False


def sync_outbox_and_cloud_reports(ctx, force=False):
    """Process Web Vault queued bot/bridge requests and route new Cloud Agent reports to personal bots."""
    global _LAST_SYNC_TS
    now = time.time()
    if not force and (now - _LAST_SYNC_TS < 45):
        return
    _LAST_SYNC_TS = now

    reg = ctx["fetch_registry"](force=False)
    bridge_reg = load_bridge_registry(ctx)
    modified = False

    # 1. Process Web Office / Vault queued bot messages or inter-bot requests
    for r in bridge_reg.get("requests", []):
        if r.get("status") != "queued_from_office":
            continue
        kind = r.get("kind") or "inter_bot"
        if kind == "hq_to_bot":
            ok_d, via, _ = deliver_bridge_message_to_target(ctx, r, reg=reg)
            r["status"] = "delivered_hq" if ok_d else "queued_inbox"
            r["delivered_via"] = via
            r["delivered_at"] = _utc_now()
            r["inbox_unread"] = not ok_d
            modified = True
        elif kind == "inter_bot":
            pk = r.get("pair_key") or bridge_pair_key(r.get("from_login"), r.get("to_login"))
            r["pair_key"] = pk
            b_active = isinstance(bridge_reg["bridges"].get(pk), dict) and bridge_reg["bridges"][pk].get("status") == "active"
            if b_active:
                ok_d, via, _ = deliver_bridge_message_to_target(ctx, r, reg=reg)
                r["status"] = "delivered_via_bridge" if ok_d else "queued_inbox_via_bridge"
                r["delivered_via"] = via
                r["delivered_at"] = _utc_now()
                r["inbox_unread"] = not ok_d
                bridge_reg["bridges"][pk]["message_count"] = int(bridge_reg["bridges"][pk].get("message_count") or 0) + 1
            else:
                r["status"] = "pending_owner"
                _send_owner_bridge_card(ctx, r)
            modified = True

    # 2. Deliver new completed Cloud Agent 24/7 work reports to requesting/assigned users' personal bots
    reports, _ = _safe_fetch_json(ctx, WORK_REPORT_FILE, branch="data", default_val=list)
    delivered_ids = set(bridge_reg.get("delivered_report_ids") or [])
    new_delivered = []
    if isinstance(reports, list):
        for entry in reports[-5:]:
            if not isinstance(entry, dict):
                continue
            cyc = entry.get("cycle", 0)
            for t in (entry.get("tasks") or []):
                if not isinstance(t, dict):
                    continue
                rep_id = f"rep:{cyc}:{t.get('issue')}"
                if rep_id in delivered_ids:
                    continue
                actor = _norm(ctx, t.get("actor_login") or "")
                proj = str(t.get("project") or _extract_project_from_issue(t.get("title") or "") or "")
                recipients = set()
                for u in (reg.get("users") or []):
                    ulg = _norm(ctx, u.get("login") or "")
                    if not ulg or ctx["is_owner_login"](ulg):
                        continue
                    pb = u.get("personal_bot") or {}
                    if pb.get("auto_send_reports") is False:
                        continue
                    if (actor and actor == ulg) or (proj and _user_can_see_project(ctx, u, proj)):
                        recipients.add(ulg)
                for r_lg in recipients:
                    route = resolve_bot_route(ctx, reg, r_lg)
                    if route["mode"] in ("personal_bot", "hq_approved_chat") and route["chat_id"] and route["bot_token"]:
                        text = (
                            f"✅ <b>Отчёт Облачных Агентов 24/7 (Директор «{_esc(route['director_name'])}»)</b>\n\n"
                            f"• Задача <b>#{_esc(t.get('issue'))}</b>: {_esc(t.get('title'))}\n"
                            f"• Агент: <i>{_esc(t.get('persona'))}</i> (<code>{_esc(t.get('model'))}</code>)\n\n"
                            f"📝 <b>Результат:</b>\n{_esc(str(t.get('preview') or '')[:550])}..."
                        )
                        ctx["call_tg"]("sendMessage", {
                            "chat_id": route["chat_id"],
                            "text": text,
                            "parse_mode": "HTML",
                            "reply_markup": build_mesh_director_keyboard(proj or "Мой Офис"),
                        }, bot_token=route["bot_token"], bot_label=route["bot_username"])
                new_delivered.append(rep_id)
                delivered_ids.add(rep_id)

    if modified or new_delivered:
        def _commit_sync(obj):
            if modified:
                by_id = {x.get("id"): x for x in bridge_reg["requests"] if isinstance(x, dict) and x.get("id")}
                for idx, cur_r in enumerate(obj["requests"]):
                    if cur_r.get("id") in by_id:
                        obj["requests"][idx] = by_id[cur_r["id"]]
                obj["bridges"].update(bridge_reg["bridges"])
            for nid in new_delivered:
                if nid not in obj["delivered_report_ids"]:
                    obj["delivered_report_ids"].append(nid)
            return obj

        save_bridge_mutated(ctx, _commit_sync, "Mesh outbox & cloud report delivery sync")
