#!/usr/bin/env python3
"""
Global Spirit Group ★ Virtual Office — 24/7 Autonomous Cloud Agent Worker (v32.0)
Runs in GitHub Actions (schedule every 15m + issue/issue_comment/workflow_dispatch):
  1. Executes open tasks & interactive task replies using GitHub Models (models.github.ai) + SSRF-safe URL reading.
  2. Persists structured Office Memory (office_memory.json), telemetry (cloud_agents_telemetry.json),
     and work logs (agent_work_report.json) on the `data` branch even when the owner's laptop is offline.
  3. Sends completed work & autonomous study reports to @unitybusinessbot (OWNER_TG_CHAT_ID).
  4. When local daemon heartbeat is stale (>180s), handles @unitybusinessbot updates under the strict
     @unityspiritpartners Request-Only Bot Entry Gate (bot_access_registry.json) without 409 conflicts.
"""

import base64
import datetime
import html
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

AGENT_MARKER = "<!-- cloud-agent-v32 -->"
DATA_BRANCH = "data"
GITHUB_API = "https://api.github.com"
MODELS_API = "https://models.github.ai/inference/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"

SKIP_ISSUE_PREFIXES = (
    "[OFFICE_DATA]",
    "[AUTH_",
    "[VAULT_",
    "[SYSTEM_",
    "[HEARTBEAT",
)

URL_RE = re.compile(r"https?://[^\s<>\"')\]}]+", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def esc_html(val):
    return html.escape(str(val or ""), quote=True)


def extract_urls(text):
    seen = set()
    out = []
    for raw in URL_RE.findall(text or ""):
        u = raw.rstrip(".,;:!?)")
        if u in seen:
            continue
        low = u.lower()
        if any(ext in low for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", "api.telegram.org")):
            continue
        seen.add(u)
        out.append(u)
    return out


def is_public_ip_host(hostname):
    if not hostname:
        return False
    low = hostname.lower().strip(".")
    if low in ("localhost", "ip6-localhost") or low.endswith(".local") or low.endswith(".internal"):
        return False
    try:
        infos = socket.getaddrinfo(low, None)
        if not infos:
            return False
        for info in infos:
            ip_str = info[4][0].split("%")[0]
            ip_obj = ipaddress.ip_address(ip_str)
            if (
                ip_obj.is_private
                or ip_obj.is_loopback
                or ip_obj.is_link_local
                or ip_obj.is_multicast
                or ip_obj.is_reserved
                or ip_obj.is_unspecified
            ):
                return False
        return True
    except Exception:
        return False


def fetch_public_url_preview(url, max_chars=2400):
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not is_public_ip_host(parsed.hostname):
            return {"url": url, "ok": False, "error": "blocked_non_public_host"}

        class NoUnsafeRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                p2 = urllib.parse.urlparse(urllib.parse.urljoin(req.full_url, newurl))
                if p2.scheme not in ("http", "https") or not is_public_ip_host(p2.hostname):
                    raise urllib.error.HTTPError(newurl, 403, "Unsafe redirect blocked", headers, None)
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        opener = urllib.request.build_opener(NoUnsafeRedirect)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "GSG-CloudAgent/32.0 (+https://globalteampartners.github.io/remote-agents-control/)",
                "Accept": "text/html,application/json,text/plain;q=0.9",
            },
        )
        with opener.open(req, timeout=8) as resp:
            raw = resp.read(140000).decode("utf-8", errors="replace")

        title_m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
        title = re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", title_m.group(1)))).strip() if title_m else ""
        cleaned = SCRIPT_STYLE_RE.sub(" ", raw)
        cleaned = html.unescape(TAG_RE.sub(" ", cleaned))
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return {"url": url, "ok": True, "title": title[:160], "excerpt": cleaned[:max_chars]}
    except Exception as exc:
        return {"url": url, "ok": False, "error": str(exc)[:140]}


def gh_request(path, token, method="GET", body=None, timeout=18):
    url = path if path.startswith("http") else f"{GITHUB_API}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "GSG-CloudAgent/32.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}


def fetch_data_json(repo, token, file_path, default_val=None):
    try:
        res = gh_request(
            f"/repos/{repo}/contents/{urllib.parse.quote(file_path)}?ref={DATA_BRANCH}&t={int(time.time())}",
            token,
        )
        raw = base64.b64decode(res["content"]).decode("utf-8")
        return json.loads(raw), res.get("sha")
    except urllib.error.HTTPError as he:
        if he.code == 404:
            return (default_val() if callable(default_val) else default_val), None
        raise
    except Exception:
        return (default_val() if callable(default_val) else default_val), None


def write_data_json(repo, token, file_path, obj, commit_msg, sha=None):
    for attempt in range(3):
        try:
            if sha is None:
                _, sha = fetch_data_json(repo, token, file_path, None)
            content_b64 = base64.b64encode(json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")).decode("ascii")
            payload = {
                "message": commit_msg,
                "content": content_b64,
                "branch": DATA_BRANCH,
            }
            if sha:
                payload["sha"] = sha
            return gh_request(f"/repos/{repo}/contents/{urllib.parse.quote(file_path)}", token, method="PUT", body=payload)
        except urllib.error.HTTPError as he:
            if he.code == 409 and attempt < 2:
                sha = None
                time.sleep(1.0)
                continue
            raise


def call_telegram(bot_token, method, data=None):
    if not bot_token:
        return None
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/{method}",
        data=json.dumps(data or {}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "GSG-CloudAgent/32.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[TG warn] {method}: {exc}", flush=True)
        return None


def call_github_models(models_token, system_prompt, user_prompt):
    if not models_token:
        return None
    payload = {
        "model": DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.35,
        "max_tokens": 900,
    }
    req = urllib.request.Request(
        MODELS_API,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {models_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "GSG-CloudAgent/32.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choices = data.get("choices") or []
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
    except Exception as exc:
        print(f"[Models warn] {exc}", flush=True)
    return None


def detect_agent_persona(text):
    low = (text or "").lower()
    if "/ceo" in low:
        return "CEO Стратег", "Дай жёсткую бизнес-оценку, ROI, приоритет монетизации и 3 конкретных шага на сегодня."
    if "/critic" in low:
        return "Критик-Аудитор", "Найди скрытые риски, слабые места и укажи как их закрыть."
    if "/viral" in low:
        return "Viral Маркетолог", "Предложи цепляющие хуки, воронку и оффер с высокой конверсией."
    if "/strategy" in low:
        return "Главный Архитектор", "Разложи задачу по этапам MVP -> продажи -> масштабирование."
    if "/expert" in low:
        return "Технический Эксперт", "Дай точную архитектуру, стек и план внедрения без воды."
    return "Облачный Совет Агентов 24/7", "Проанализируй задачу, извлеки пользу из ссылок/контекста, сформулируй решение и чёткий следующий шаг."


def build_fallback_reply(issue_title, prompt_text, previews, persona_name):
    lines = [
        f"🤖 **24/7 Облачный Агент ({persona_name}) выполнил разбор в облаке**",
        "",
        f"**Задача:** {issue_title}",
    ]
    if previews:
        lines.append("\n**Изученные материалы:**")
        for p in previews:
            if p.get("ok"):
                t = p.get("title") or p.get("url")
                lines.append(f"- 🔗 **{t}** (`{p['url']}`): {p.get('excerpt', '')[:260]}...")
            else:
                lines.append(f"- ⚠️ Не удалось открыть `{p['url']}` ({p.get('error', 'error')})")
    lines.extend([
        "",
        "**Итоговые шаги выполнения:**",
        "1. Зафиксирован контекст задачи и сохранён в облачной памяти офиса (`office_memory.json`).",
        "2. Подготовлен план дальнейшего действия — напишите уточнение или команду (`/ceo`, `/strategy`, `/viral`, `/expert`) в ответ, и облачный агент продолжит автоматически даже при выключенном ноутбуке.",
    ])
    return "\n".join(lines)


def default_office_memory():
    return {
        "version": "32.0",
        "updated_at": utc_now_iso(),
        "cycle_count": 0,
        "processed_triggers": [],
        "studied_urls": [],
        "learned_facts": [],
        "projects": {},
        "recent_events": [],
    }


def append_memory_event(mem, kind, summary, issue_num=None, url=None):
    events = mem.setdefault("recent_events", [])
    events.append({
        "at": utc_now_iso(),
        "kind": kind,
        "issue": issue_num,
        "url": url,
        "summary": str(summary)[:320],
    })
    mem["recent_events"] = events[-80:]


def append_learned_fact(mem, topic, insight, source=""):
    facts = mem.setdefault("learned_facts", [])
    facts.append({
        "at": utc_now_iso(),
        "topic": str(topic)[:140],
        "insight": str(insight)[:600],
        "source": str(source)[:180],
    })
    mem["learned_facts"] = facts[-60:]


def should_skip_issue(issue):
    title = (issue.get("title") or "").strip()
    upper = title.upper()
    return any(upper.startswith(pref) for pref in SKIP_ISSUE_PREFIXES)


def execute_issue_task(repo, gh_token, models_token, issue, comments, mem, trigger_key):
    num = issue["number"]
    title = issue.get("title") or f"Задача #{num}"
    body = issue.get("body") or ""
    human_comments = [c for c in comments if AGENT_MARKER not in (c.get("body") or "")]
    latest_human = human_comments[-1].get("body", "") if human_comments else ""

    source_text = f"{title}\n\n{body}\n\nПоследний запрос: {latest_human}"
    urls = extract_urls(source_text)[:2]
    previews = [fetch_public_url_preview(u) for u in urls]

    persona_name, persona_goal = detect_agent_persona(latest_human or body or title)
    recent_facts = "\n".join(
        f"- [{f.get('topic')}]: {f.get('insight', '')[:220]}"
        for f in (mem.get("learned_facts") or [])[-8:]
    )
    url_ctx = "\n".join(
        f"- URL {p['url']} ({p.get('title', '')}): {p.get('excerpt', '')[:1200]}"
        for p in previews if p.get("ok")
    )

    system_prompt = (
        f"Ты — {persona_name} в системе Global Spirit Group ★ Virtual Office. "
        f"Отвечай чётко на русском языке, структурированно, практически и с фокусом на измеримый результат. "
        f"{persona_goal}"
    )
    user_prompt = (
        f"Задача #{num}: {title}\n\n"
        f"Описание:\n{body[:1800]}\n\n"
        f"Последние сообщения пользователя:\n{latest_human[:1200]}\n\n"
        f"Память офиса:\n{recent_facts or 'пока пусто'}\n\n"
        f"Содержимое ссылок:\n{url_ctx or 'ссылок нет'}"
    )

    ai_text = call_github_models(models_token, system_prompt, user_prompt)
    used_model = DEFAULT_MODEL if ai_text else "deterministic-fallback"
    if not ai_text:
        ai_text = build_fallback_reply(title, latest_human or body, previews, persona_name)

    final_comment = (
        f"🤖 **{persona_name} · 24/7 Облако ({used_model})**\n\n"
        f"{ai_text}\n\n"
        f"---\n_Память офиса обновлена · Работа выполнена автономно в облаке_ {AGENT_MARKER}"
    )

    gh_request(f"/repos/{repo}/issues/{num}/comments", gh_token, method="POST", body={"body": final_comment})

    processed = mem.setdefault("processed_triggers", [])
    if trigger_key not in processed:
        processed.append(trigger_key)
    mem["processed_triggers"] = processed[-250:]
    for p in previews:
        if p.get("ok") and p["url"] not in mem.setdefault("studied_urls", []):
            mem["studied_urls"].append(p["url"])
    mem["studied_urls"] = mem.get("studied_urls", [])[-200:]

    append_learned_fact(mem, f"Задача #{num}: {title}", ai_text[:450], source=f"issue #{num}")
    append_memory_event(mem, "cloud_task_executed", f"#{num} ({persona_name}): {title}", issue_num=num)
    return {
        "issue": num,
        "title": title,
        "persona": persona_name,
        "model": used_model,
        "preview": ai_text[:300],
    }


def run_cloud_study_cycle(repo, gh_token, models_token, mem):
    kb, _ = fetch_data_json(repo, gh_token, "knowledge_base_global.json", default_val=list)
    studied = set(mem.get("studied_urls") or [])
    candidates = []
    if isinstance(kb, list):
        for item in kb:
            url = (item.get("url") or "").strip() if isinstance(item, dict) else ""
            if url and url not in studied:
                candidates.append((item.get("title") or url, url))
    elif isinstance(kb, dict):
        for item in kb.get("items", []):
            url = (item.get("url") or "").strip() if isinstance(item, dict) else ""
            if url and url not in studied:
                candidates.append((item.get("title") or url, url))

    if not candidates:
        return None

    title, url = candidates[0]
    preview = fetch_public_url_preview(url)
    mem.setdefault("studied_urls", []).append(url)
    mem["studied_urls"] = mem["studied_urls"][-200:]
    if not preview.get("ok"):
        append_memory_event(mem, "study_skip", f"URL недоступен: {url}", url=url)
        return None

    summary = call_github_models(
        models_token,
        "Ты — ИИ-аналитик Базы Знаний Virtual Office. Сделай выжимку из страницы на русском языке (3-5 буллетов: главная суть, идеи заработка, как применить в наших проектах).",
        f"Материал: {title}\nURL: {url}\nТекст:\n{preview.get('excerpt', '')[:2200]}",
    )
    if not summary:
        summary = f"Изучена страница «{preview.get('title') or title}»: {preview.get('excerpt', '')[:280]}"

    append_learned_fact(mem, title, summary, source=url)
    append_memory_event(mem, "cloud_study", f"Изучен материал: {title}", url=url)
    return {"title": title, "url": url, "summary": summary[:320]}


def handle_offline_telegram_fallback(repo, gh_token, bot_token, owner_chat_id):
    """Only drains Telegram updates when local daemon heartbeat is missing or >180s stale."""
    if not bot_token or not owner_chat_id:
        return {"polled": False, "reason": "no_tg_config"}

    hb, _ = fetch_data_json(repo, gh_token, "local_daemon_heartbeat.json", default_val=dict)
    now_ts = time.time()
    hb_ts = float((hb or {}).get("ts") or 0)
    if hb_ts and (now_ts - hb_ts) <= 180:
        return {"polled": False, "reason": "local_daemon_online"}

    access, sha = fetch_data_json(
        repo,
        gh_token,
        "bot_access_registry.json",
        default_val=lambda: {"mode": "request_only", "owner_chat_id": str(owner_chat_id), "approved": {}, "pending": {}, "banned": {}, "cloud_tg_offset": 0},
    )
    offset = int(access.get("cloud_tg_offset") or 0)
    res = call_telegram(bot_token, "getUpdates", {"offset": offset, "timeout": 1})
    if not res or not res.get("ok"):
        return {"polled": True, "updates": 0, "reason": "tg_empty_or_busy"}

    updates = res.get("result") or []
    dirty = False
    handled = 0

    for item in updates:
        offset = max(offset, int(item.get("update_id", 0)) + 1)
        dirty = True
        cq = item.get("callback_query")
        msg = item.get("message")

        if cq:
            cq_id = cq["id"]
            data = cq.get("data", "")
            from_u = cq.get("from", {})
            c_id = str(cq.get("message", {}).get("chat", {}).get("id") or from_u.get("id") or "")
            is_owner = str(c_id) == str(owner_chat_id) or str(from_u.get("id")) == str(owner_chat_id)

            if is_owner and data.startswith("bot_gate_ok:"):
                t_cid = data.split("bot_gate_ok:", 1)[1].strip()
                src = access.setdefault("pending", {}).pop(t_cid, None) or access.setdefault("banned", {}).pop(t_cid, None) or {"chat_id": t_cid}
                src["approved_by"] = "@unityspiritpartners"
                src["approved_at"] = utc_now_iso()
                access.setdefault("approved", {})[t_cid] = src
                call_telegram(bot_token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": "✅ Доступ в бот одобрен (Облако 24/7)"})
                call_telegram(bot_token, "sendMessage", {"chat_id": owner_chat_id, "text": f"✅ Доступ в @unitybusinessbot одобрен для <code>{esc_html(t_cid)}</code>.", "parse_mode": "HTML"})
                call_telegram(bot_token, "sendMessage", {"chat_id": t_cid, "text": "✅ <b>@unityspiritpartners одобрил ваш вход в @unitybusinessbot!</b>", "parse_mode": "HTML"})
                handled += 1
                continue
            elif is_owner and data.startswith("bot_gate_ban:"):
                t_cid = data.split("bot_gate_ban:", 1)[1].strip()
                src = access.setdefault("pending", {}).pop(t_cid, None) or access.setdefault("approved", {}).pop(t_cid, None) or {"chat_id": t_cid}
                src["banned_at"] = utc_now_iso()
                access.setdefault("banned", {})[t_cid] = src
                call_telegram(bot_token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": "🚫 Доступ отклонён"})
                call_telegram(bot_token, "sendMessage", {"chat_id": t_cid, "text": "🚫 Запрос на вход в @unitybusinessbot отклонён @unityspiritpartners."})
                handled += 1
                continue
            elif not is_owner and c_id not in (access.get("approved") or {}):
                call_telegram(bot_token, "answerCallbackQuery", {"callback_query_id": cq_id, "text": "⏳ Вход в бот только по одобрению @unityspiritpartners.", "show_alert": True})
                continue

        if msg and "text" in msg:
            c_id = str(msg["chat"]["id"])
            from_u = msg.get("from", {})
            txt = msg["text"].strip()
            is_owner = str(c_id) == str(owner_chat_id) or str(from_u.get("id")) == str(owner_chat_id)
            if not is_owner:
                if c_id in (access.get("banned") or {}):
                    call_telegram(bot_token, "sendMessage", {"chat_id": c_id, "text": "🚫 Доступ к @unitybusinessbot закрыт."})
                elif c_id not in (access.get("approved") or {}):
                    uname = from_u.get("username") or ""
                    fname = from_u.get("first_name") or "Пользователь"
                    prev = float(((access.get("pending") or {}).get(c_id) or {}).get("last_notified_ts") or 0)
                    notify_owner = (now_ts - prev) >= 120
                    access.setdefault("pending", {})[c_id] = {
                        "chat_id": c_id,
                        "username": uname,
                        "first_name": fname,
                        "last_text": txt[:180],
                        "requested_at": utc_now_iso(),
                        "last_notified_ts": now_ts if notify_owner else prev,
                    }
                    if notify_owner:
                        who = f"@{esc_html(uname)} ({esc_html(fname)})" if uname else esc_html(fname)
                        call_telegram(bot_token, "sendMessage", {
                            "chat_id": owner_chat_id,
                            "text": (
                                f"🔒 <b>ЗАПРОС НА ВХОД В @unitybusinessbot (24/7 Облако)</b>\n\n"
                                f"• Пользователь: {who}\n• Chat ID: <code>{esc_html(c_id)}</code>\n• Текст: <code>{esc_html(txt[:160])}</code>"
                            ),
                            "parse_mode": "HTML",
                            "reply_markup": {"inline_keyboard": [[
                                {"text": "✅ Одобрить вход в бот", "callback_data": f"bot_gate_ok:{c_id}"},
                                {"text": "🚫 Отклонить", "callback_data": f"bot_gate_ban:{c_id}"},
                            ]]},
                        })
                    call_telegram(bot_token, "sendMessage", {
                        "chat_id": c_id,
                        "text": f"🔒 <b>Вход в @unitybusinessbot — только по запросу к @unityspiritpartners</b>\n\nЗапрос (Chat ID: <code>{esc_html(c_id)}</code>) отправлен.",
                        "parse_mode": "HTML",
                    })
                    handled += 1
                    continue
                elif c_id in (access.get("approved") or {}):
                    call_telegram(bot_token, "sendMessage", {
                        "chat_id": c_id,
                        "text": "✅ Вы авторизованы в @unitybusinessbot. Облачный режим 24/7 принял сообщение.",
                    })
                    handled += 1
            elif is_owner and txt.startswith("/status"):
                call_telegram(bot_token, "sendMessage", {"chat_id": owner_chat_id, "text": "☁️ <b>24/7 Облачные агенты активны!</b> Ноутбук сейчас офлайн — команды и одобрения работают через облако.", "parse_mode": "HTML"})
                handled += 1

    if dirty:
        access["cloud_tg_offset"] = offset
        write_data_json(repo, gh_token, "bot_access_registry.json", access, "Cloud 24/7 bot gate sync [v32.0]", sha=sha)

    return {"polled": True, "updates": len(updates), "handled": handled}


def main():
    repo = os.environ.get("GITHUB_REPOSITORY", "GlobalTeamPartners/remote-agents-control")
    gh_token = os.environ.get("GTP_SYNC_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    models_token = os.environ.get("GITHUB_TOKEN") or gh_token
    bot_token = os.environ.get("TG_BOT_TOKEN") or ""
    owner_chat_id = os.environ.get("OWNER_TG_CHAT_ID") or "8538272428"
    event_name = os.environ.get("GITHUB_EVENT_NAME", "schedule")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    dispatch_mode = os.environ.get("DISPATCH_MODE", "auto")
    dispatch_issue = (os.environ.get("DISPATCH_ISSUE") or "").strip()

    if not gh_token:
        print("Missing GITHUB_TOKEN / GTP_SYNC_TOKEN", file=sys.stderr)
        sys.exit(1)

    event_payload = {}
    if event_path and os.path.exists(event_path):
        try:
            with open(event_path, "r", encoding="utf-8") as ef:
                event_payload = json.load(ef)
        except Exception:
            event_payload = {}

    if event_name == "issue_comment":
        c_body = (event_payload.get("comment") or {}).get("body") or ""
        if AGENT_MARKER in c_body:
            print("Self-comment detected, skipping cleanly.")
            return

    mem, mem_sha = fetch_data_json(repo, gh_token, "office_memory.json", default_val=default_office_memory)
    if not isinstance(mem, dict):
        mem = default_office_memory()
    mem["cycle_count"] = int(mem.get("cycle_count") or 0) + 1
    mem["updated_at"] = utc_now_iso()

    executed_tasks = []
    study_result = None

    # 1. Handle immediate issue / issue_comment / targeted workflow_dispatch
    target_issue_nums = []
    if event_name in ("issues", "issue_comment") and event_payload.get("issue"):
        target_issue_nums.append((event_payload["issue"]["number"], event_payload.get("comment", {}).get("id")))
    elif dispatch_issue.isdigit():
        target_issue_nums.append((int(dispatch_issue), "manual"))

    for num, cid in target_issue_nums:
        try:
            issue = gh_request(f"/repos/{repo}/issues/{num}", gh_token)
            if should_skip_issue(issue):
                continue
            trig = f"issue:{num}:comment:{cid or 'open'}"
            if trig in (mem.get("processed_triggers") or []):
                continue
            comments = gh_request(f"/repos/{repo}/issues/{num}/comments?per_page=30", gh_token)
            res = execute_issue_task(repo, gh_token, models_token, issue, comments, mem, trig)
            executed_tasks.append(res)
        except Exception as exc:
            print(f"[Issue #{num} error] {exc}", flush=True)

    # 2. On schedule or workflow_dispatch, scan open tasks needing cloud response (max 2 per cycle)
    if event_name in ("schedule", "workflow_dispatch") and dispatch_mode != "study":
        try:
            open_issues = gh_request(f"/repos/{repo}/issues?state=open&sort=updated&direction=desc&per_page=15", gh_token)
            for iss in open_issues:
                if len(executed_tasks) >= 2:
                    break
                if iss.get("pull_request") or should_skip_issue(iss):
                    continue
                num = iss["number"]
                comments = gh_request(f"/repos/{repo}/issues/{num}/comments?per_page=25", gh_token)
                last_human_idx = -1
                last_agent_idx = -1
                for idx, cm in enumerate(comments):
                    if AGENT_MARKER in (cm.get("body") or ""):
                        last_agent_idx = idx
                    else:
                        last_human_idx = idx
                # Execute if never replied by cloud-agent-v32 OR human wrote after last agent comment
                if last_agent_idx == -1 or last_human_idx > last_agent_idx:
                    last_cid = comments[last_human_idx]["id"] if last_human_idx >= 0 else "init"
                    trig = f"issue:{num}:auto:{last_cid}"
                    if trig in (mem.get("processed_triggers") or []):
                        continue
                    res = execute_issue_task(repo, gh_token, models_token, iss, comments, mem, trig)
                    executed_tasks.append(res)
        except Exception as exc:
            print(f"[Scan open issues error] {exc}", flush=True)

    # 3. Continuous study cycle on schedule / workflow_dispatch
    if event_name in ("schedule", "workflow_dispatch"):
        try:
            study_result = run_cloud_study_cycle(repo, gh_token, models_token, mem)
        except Exception as exc:
            print(f"[Study cycle error] {exc}", flush=True)

    # 4. Offline Telegram Bot Gate polling fallback
    tg_poll_status = {"polled": False}
    try:
        tg_poll_status = handle_offline_telegram_fallback(repo, gh_token, bot_token, owner_chat_id)
    except Exception as exc:
        print(f"[TG offline fallback error] {exc}", flush=True)

    # 5. Persist Office Memory + Cloud Telemetry + Work Report
    try:
        write_data_json(repo, gh_token, "office_memory.json", mem, f"Cloud 24/7 memory cycle #{mem['cycle_count']} [v32.0]", sha=mem_sha)
    except Exception as exc:
        print(f"[Memory save warn] {exc}", flush=True)

    telemetry = {
        "version": "32.0",
        "status": "online_24_7",
        "last_cycle_iso": utc_now_iso(),
        "cycle_count": mem["cycle_count"],
        "event": event_name,
        "tasks_executed_this_cycle": len(executed_tasks),
        "studied_this_cycle": bool(study_result),
        "memory_facts_count": len(mem.get("learned_facts") or []),
        "studied_urls_count": len(mem.get("studied_urls") or []),
        "bot_gate_mode": "request_only_by_@unityspiritpartners",
        "tg_offline_fallback": tg_poll_status,
    }
    try:
        write_data_json(repo, gh_token, "cloud_agents_telemetry.json", telemetry, f"Cloud telemetry cycle #{mem['cycle_count']} [v32.0]")
    except Exception as exc:
        print(f"[Telemetry save warn] {exc}", flush=True)

    if executed_tasks or study_result or event_name == "workflow_dispatch":
        rep, rep_sha = fetch_data_json(repo, gh_token, "agent_work_report.json", default_val=list)
        if not isinstance(rep, list):
            rep = []
        rep.append({
            "at": utc_now_iso(),
            "source": "github_actions_cloud_24_7",
            "cycle": mem["cycle_count"],
            "tasks": executed_tasks,
            "study": study_result,
        })
        rep = rep[-60:]
        try:
            write_data_json(repo, gh_token, "agent_work_report.json", rep, f"Cloud work report cycle #{mem['cycle_count']} [v32.0]", sha=rep_sha)
        except Exception as exc:
            print(f"[Report save warn] {exc}", flush=True)

    # 6. Notify @unitybusinessbot with completed work / study report
    should_notify = bool(executed_tasks or study_result or event_name == "workflow_dispatch" or (mem["cycle_count"] % 16 == 0))
    if should_notify and bot_token and owner_chat_id:
        tg_lines = [
            f"☁️ <b>ОТЧЁТ ОБЛАЧНЫХ АГЕНТОВ 24/7 (Цикл #{mem['cycle_count']})</b>",
            f"• Режим: <b>Автономно в облаке GitHub (даже без ноутбука)</b>",
            f"• Память офиса: <b>{len(mem.get('learned_facts') or [])} знаний</b> | <b>{len(mem.get('studied_urls') or [])} ссылок</b>",
        ]
        if executed_tasks:
            tg_lines.append("\n<b>✅ Выполненные задачи:</b>")
            for t in executed_tasks:
                tg_lines.append(f"• <b>#{t['issue']} {esc_html(t['title'][:70])}</b> ({esc_html(t['persona'])})")
                tg_lines.append(f"  <i>{esc_html(t['preview'][:200])}...</i>")
        if study_result:
            tg_lines.append(f"\n<b>🧠 Автоизучение в облаке:</b> {esc_html(study_result['title'][:80])}")
            tg_lines.append(f"<i>{esc_html(study_result['summary'][:220])}...</i>")
        if not executed_tasks and not study_result:
            tg_lines.append("\n• Статус: Новых необработанных задач нет, система в полном боевом режиме 24/7.")

        tg_lines.append(f"\n🔐 Бот-шлюз: <b>@unityspiritpartners Request-Only</b>")
        call_telegram(bot_token, "sendMessage", {
            "chat_id": owner_chat_id,
            "text": "\n".join(tg_lines)[:3900],
            "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[
                {"text": "🏢 Открыть Офис (v32.0)", "url": "https://globalteampartners.github.io/remote-agents-control/?v=32.0"}
            ]]},
        })

    print(json.dumps(telemetry, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
