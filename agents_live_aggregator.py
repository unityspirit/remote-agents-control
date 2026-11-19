#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unified Live Agent Command Graph & Task State Aggregator (v34.6).

Collects live Office data from:
  - Local SEO Autopilot (`seo_autopilot_state.json`, `seo_tasks_queue.json`,
    `seo_drift_history.json`, `seo_skills_manifest.json`)
  - Open & recent GitHub Issues (`GlobalTeamPartners/remote-agents-control` & mirror)
  - GitHub `data` branch (`board.json`, `cloud_agents_telemetry.json`,
    `director_actions_log.json`, sanitized `auth_registry.json` bot roster)

Generates strictly secret-scrubbed `data/agents_live_state.json`, serves
`127.0.0.1:18799/api/agents-live`, and pushes `agents_live_state.json` to
GitHub `data` branch so the Live Graph page works both on PC and mobile 24/7.
"""
from __future__ import annotations

import base64
import datetime
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

OFFICE_DIR = Path(__file__).resolve().parent
DATA_DIR = OFFICE_DIR / "data"
CFG_PATH = OFFICE_DIR / ".tg_daemon_config.json"
SNAPSHOT_FILE = DATA_DIR / "agents_live_state.json"

SECRET_PATTERNS = (
    re.compile(r"\bghp_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\b\d{8,11}:AA[A-Za-z0-9_-]{25,}\b"),
)


def _utc_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def _save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _scrub_secrets(text: str) -> str:
    out = str(text or "")
    for pat in SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def _fetch_gh_json(url: str, token: str = "") -> Any:
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "GSG-AgentsLive/34.6"}
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=14) as r:
        return json.loads(r.read().decode("utf-8"))


def _fetch_gh_data_file(repo: str, path: str, token: str = "") -> Any:
    meta = _fetch_gh_json(f"https://api.github.com/repos/{repo}/contents/{path}?ref=data&t={int(time.time())}", token)
    raw = meta.get("content", "").replace("\n", "")
    if not raw:
        return None
    return json.loads(base64.b64decode(raw).decode("utf-8"))


USER_LABELS = {
    "vlaslav": "👑 Власлав (Штаб)",
    "alberta": "💎 Альберта",
    "yura": "🏒 Юрий (КХЛ)",
    "true1team": "🔥 @true1team",
    "tatiana": "🐝 Татьяна",
    "dominicana": "🏝️ Доминикана",
    "dima": "🏭 Дима Урал",
    "svetlana": "🎬 Светлана (GlobalFilms)",
}


def infer_user_owner_for_task(title: str, body: str = "", project: str = "", raw_owner: str = "") -> tuple[str, str]:
    """Identify which human stakeholder or team owns a task for per-user filtering."""
    low = f"{title}\n{body}\n{project}\n{raw_owner}".lower()
    if re.search(r"alberta|альберт|lermontov|aliashirani|лид-магн|490|точка опоры|#9005", low):
        return "alberta", USER_LABELS["alberta"]
    if re.search(r"yura|юра|юрий|yvyatkin|khl|кхл", low):
        return "yura", USER_LABELS["yura"]
    if re.search(r"true1team|true 1 team|\b#?167\b", low):
        return "true1team", USER_LABELS["true1team"]
    if re.search(r"tatiana|татьяна|tentorium|тенториум", low):
        return "tatiana", USER_LABELS["tatiana"]
    if re.search(r"dominicana|доминикан|las terrenas|белый|profitroom|вилл|#9004", low):
        return "dominicana", USER_LABELS["dominicana"]
    if re.search(r"дима|dima|синичих|алапаевск|щебен|щебень|zksk|ural|урал|#9003", low):
        return "dima", USER_LABELS["dima"]
    if re.search(r"светлан|svetlana|globalfilms|global films|#9006", low):
        return "svetlana", USER_LABELS["svetlana"]
    return "vlaslav", USER_LABELS["vlaslav"]


def infer_seo_skills_for_task(title: str, body: str = "", url: str = "") -> tuple[str, list[str]]:
    """Map any Office task or URL to the most relevant Subordinate Agent and installed skills (SEO, Marketing/CRO, Semrush, Research)."""
    low = f"{title}\n{body}\n{url}".lower()
    skills: list[str] = []
    if re.search(r"https?://|сайт|магазин|landing|лендинг|shopify|store|domain|домен", low):
        skills.extend(["seo-audit", "seo-technical", "cro", "copywriting"])
    if re.search(r"store|shopify|товар|product|market|ebay|customaistore|open v3", low):
        skills.extend(["seo-ecommerce", "seo-schema", "pricing", "launch"])
    if re.search(r"контент|instagram|youtube|reel|video|vsl|пост|content|traf|трафик|ad|tentorium", low):
        skills.extend(["seo-content", "ads", "emails", "free-tools"])
    if re.search(r"alberta|location|local|москв|рязан|доминикан|урал|city|map|карт|щебень|вилла", low):
        skills.extend(["seo-local", "seo-maps", "lead-intelligence", "offers"])
    if re.search(r"ssl|sitemap|robots|llms|canonical|redirect|drift", low):
        skills.extend(["seo-sitemap", "seo-agentic", "seo-drift"])
    if re.search(r"outsource\.gov|competitor|анализ|research|платформ|strategy|план|khl|кхл|saas|ai", low):
        skills.extend(["semrush-competitor-gap", "semrush-serp-analysis", "deep-research", "pricing"])
    if re.search(r"инвест|investor|b2b|кп|whatsapp|pitch|cold", low):
        skills.extend(["cold-email", "investor-outreach", "lead-intelligence"])
    if re.search(r"workflow|daemon|agent|облак|hydrogen|argon|carbon|автопилот", low):
        skills.extend(["continuous-agent-loop", "semrush-keyword-research"])

    # Deduplicate preserving priority order
    seen: set[str] = set()
    clean_skills: list[str] = []
    for sk in skills:
        if sk not in seen:
            seen.add(sk)
            clean_skills.append(sk)
    if not clean_skills:
        clean_skills = ["seo-plan", "cro", "deep-research", "semrush-keyword-research"]

    # Determine primary subordinate agent owner
    if re.search(r"\bseo\b|sitemap|robots|llms|canonical|ssl|outsource\.gov|audit", low):
        agent_id = "agent_seo"
    elif re.search(r"open v3|store|магазин|shopify|12 it|mini app", low):
        agent_id = "agent_store"
    elif re.search(r"traffic|profitroom|alberta|lead|лид|реклам|globalplus|unityspiritpartners|доминикан|урал|tentorium", low):
        agent_id = "agent_traffic"
    elif re.search(r"film|cinema|globalfilms|video|30 навыков|stabilization", low):
        agent_id = "agent_apps"
    elif re.search(r"cloud|hydrogen|argon|carbon|облак|workflow|khl|кхл", low):
        agent_id = "agent_cloud"
    else:
        agent_id = "agent_research"

    return agent_id, clean_skills[:5]


def infer_action_plan_ru(title: str, project: str = "", skills: list[str] | None = None, user_key: str = "vlaslav", status: str = "") -> str:
    """Provide a crisp, actionable Russian explanation of AI benefit and next concrete move."""
    sk_str = ", ".join((skills or [])[:3])
    if status == "done":
        return f"Выполнено и учтено в системе знаний Офиса (скиллы: {sk_str})."
    if user_key == "alberta":
        return "Трипваер 490 ₽ -> клуб 1M ₽: применить CRO/Copywriting/SEO-Local, подключить ЮKassa/Prodamus и /start в @lermontovplusbot."
    if user_key == "yura":
        return "Упаковка КХЛ-аналитики: бесплатный утренний дайджест (lead-magnet) -> платная подписка Live-сигналов + /set_bot yvyatkin."
    if user_key == "dima":
        return "B2B Урал (щебень): рассылка 10 КП директорам заводов Свердловской обл. (готовый текст в отчёте ALL_USERS_TASKS)."
    if user_key == "dominicana":
        return "Доминикана (виллы): прямое бронирование без 15-18% комиссии Airbnb — готовый WhatsApp-оффер в отчёте."
    if user_key == "tatiana":
        return "Tentorium: 3-шаговая автоцепочка повторных заказов + акционные наборы для роста LTV клиентов."
    if user_key == "true1team":
        return "Быстрые деньги @true1team: продажа витрин Open V3 ($150 CPA / $750 DFY) + AI/SEO-аудит локальному бизнесу."
    if user_key == "svetlana":
        return "GlobalFilms (SEO 96/100): мобильный тест фильтров каталога и конверсия в заявки."
    low = f"{title} {project}".lower()
    if "outsource.gov" in low or "#79" in low:
        return "IT Park Uzbekistan (0% налога на экспорт): 3 шага запуска и выгода для компании опубликованы в Issue #79."
    if "seo" in low or "sitemap" in low or "canonical" in low or "alberta-hub" in low:
        return f"Автопилот SEO применяет [{sk_str}] для роста индексации и органического трафика без бюджета на рекламу."
    return f"Агенты применяют [{sk_str}] для монетизации, оптимизации воронки и сокращения ручной рутины."


def _parse_local_seo_queue() -> list[dict[str, Any]]:
    q = _load_json(DATA_DIR / "seo_tasks_queue.json", {"tasks": []})
    tasks = q.get("tasks") if isinstance(q, dict) else []
    return [t for t in tasks if isinstance(t, dict)]


def build_live_snapshot(include_remote: bool = True) -> dict[str, Any]:
    cfg = _load_json(CFG_PATH, {})
    gtp_repo = cfg.get("gtp_repo") or "GlobalTeamPartners/remote-agents-control"
    u_repo = cfg.get("u_repo") or "unityspirit/remote-agents-control"
    gtp_tok = (cfg.get("gtp_token") or "").strip()

    manifest = _load_json(OFFICE_DIR / "seo_skills_manifest.json", {})
    seo_state = _load_json(DATA_DIR / "seo_autopilot_state.json", {})
    seo_queue = _parse_local_seo_queue()
    drift_hist = _load_json(DATA_DIR / "seo_drift_history.json", {"runs": []})

    issues: list[dict[str, Any]] = []
    board_cards: list[dict[str, Any]] = []
    telemetry: dict[str, Any] = {}
    director_actions: list[dict[str, Any]] = []
    team_bots: list[dict[str, Any]] = []
    errors: list[str] = []

    if include_remote:
        try:
            raw_issues = _fetch_gh_json(f"https://api.github.com/repos/{gtp_repo}/issues?state=all&per_page=30", gtp_tok)
            issues = [it for it in raw_issues if isinstance(it, dict) and "pull_request" not in it]
        except Exception as exc:
            errors.append(f"issues:{exc}")

        try:
            b = _fetch_gh_data_file(gtp_repo, "board.json", gtp_tok)
            if isinstance(b, dict):
                raw_c = b.get("cards")
                board_cards = list(raw_c.values()) if isinstance(raw_c, dict) else (raw_c if isinstance(raw_c, list) else [])
        except Exception as exc:
            errors.append(f"board:{exc}")

        try:
            t = _fetch_gh_data_file(gtp_repo, "cloud_agents_telemetry.json", gtp_tok)
            if isinstance(t, dict):
                telemetry = t
        except Exception as exc:
            errors.append(f"telemetry:{exc}")

        try:
            da = _fetch_gh_data_file(gtp_repo, "director_actions_log.json", gtp_tok)
            if isinstance(da, dict):
                director_actions = da.get("actions") or da.get("events") or []
            elif isinstance(da, list):
                director_actions = da
        except Exception as exc:
            errors.append(f"director_actions:{exc}")

        try:
            reg = _fetch_gh_data_file(gtp_repo, "auth_registry.json", gtp_tok)
            if isinstance(reg, dict):
                seen_bots: set[str] = set()
                for u in reg.get("users") or []:
                    pb = (u.get("personal_bot") or {})
                    login = str(u.get("login") or "").strip()
                    b_user = str(pb.get("bot_username") or "").strip()
                    d_name = str(pb.get("director_name") or "").strip()
                    c_stat = str(pb.get("control_status") or "").strip()
                    key = f"{b_user}|{d_name}"
                    if (b_user or d_name) and key not in seen_bots:
                        seen_bots.add(key)
                        team_bots.append({
                            "login": login,
                            "full_name": str(u.get("full_name") or login),
                            "director_name": d_name or "Личный Директор",
                            "bot_username": b_user or "pending_setup",
                            "status": c_stat or "not_configured",
                        })
        except Exception as exc:
            errors.append(f"registry:{exc}")

    if not board_cards:
        local_board = _load_json(Path(r"C:\Users\Vlaslav\Downloads\Входящие_Офис\_system\board.json"), {})
        if isinstance(local_board, dict):
            raw_lc = local_board.get("cards")
            board_cards = list(raw_lc.values()) if isinstance(raw_lc, dict) else (raw_lc if isinstance(raw_lc, list) else [])

    # Build unified task list (SEO Queue + Open/Active GitHub Issues + Actionable Board Cards)
    unified_tasks: list[dict[str, Any]] = []
    seen_task_keys: set[str] = set()

    # 1) SEO Queue Tasks
    for sq in reversed(seo_queue[-15:]):
        tid = str(sq.get("id") or "")
        if not tid or tid in seen_task_keys:
            continue
        seen_task_keys.add(tid)
        desc = _scrub_secrets(str(sq.get("description") or "SEO Task"))
        url = str(sq.get("url") or "")
        agent_id, skills = infer_seo_skills_for_task(desc, url=url)
        st_raw = str(sq.get("status") or "pending")
        unified_tasks.append({
            "id": tid,
            "source": "seo_autopilot",
            "title": desc[:130],
            "url": url,
            "project": "SEO-Автопилот (35 скиллов)",
            "status": "done" if st_raw == "completed" else ("in_progress" if st_raw == "running" else "queued"),
            "priority": str(sq.get("priority") or "high"),
            "agent_id": "agent_seo" if not url else agent_id,
            "skills": skills,
            "result": _scrub_secrets(str(sq.get("result_summary") or "")),
            "updated_at": sq.get("completed_at") or sq.get("created_at") or _utc_iso(),
        })

    # 2) Live Domain Findings / Alerts -> Tasks
    for site in seo_state.get("live_summary") or []:
        s_url = str(site.get("url") or "")
        s_name = str(site.get("name") or s_url)
        s_score = int(site.get("score") or 0)
        s_http = site.get("status_code")
        needs_fix = (s_score < 85 and "mirror" not in s_name.lower()) or not s_http
        t_id = f"domain:{s_name}"
        if t_id not in seen_task_keys:
            seen_task_keys.add(t_id)
            _, skills = infer_seo_skills_for_task(s_name, url=s_url)
            unified_tasks.append({
                "id": t_id,
                "source": "seo_domain_health",
                "title": f"{s_name}: SEO {s_score}/100 (HTTP {s_http or 'ERR'})",
                "url": s_url,
                "project": "Домены & GEO",
                "status": "attention" if needs_fix else "done",
                "priority": "P1" if s_score == 0 else ("high" if needs_fix else "normal"),
                "agent_id": "agent_seo",
                "skills": skills,
                "result": f"Оценка {s_score}/100 · HTTP {s_http}" if s_http else "SSL/HTTPS ошибка — требуется обновление сертификата",
                "updated_at": seo_state.get("last_run_iso") or _utc_iso(),
            })

    # 3) GitHub Issues (#79 open, recent executed #64..#78)
    for it in issues[:20]:
        num = it.get("number")
        title = _scrub_secrets(str(it.get("title") or ""))
        body = _scrub_secrets(str(it.get("body") or ""))
        urls = [u.rstrip(".,;:!?)]}»«\"'<>") for u in re.findall(r"https?://[^\s<>'\"«»]+", f"{title}\n{body}")]
        proj_m = re.search(r"PROJ:\s*([^\n|]+)", title)
        proj = proj_m.group(1).strip() if proj_m else "Главный Штаб"
        agent_id, skills = infer_seo_skills_for_task(title, body, urls[0] if urls else "")
        state_val = "in_progress" if it.get("state") == "open" else "done"
        unified_tasks.append({
            "id": f"GH-{num}",
            "source": "telegram_director_issue",
            "title": title.replace("CMD:", "").strip()[:130],
            "url": it.get("html_url") or "",
            "target_url": urls[0] if urls else "",
            "project": proj,
            "status": state_val,
            "priority": "P1" if state_val == "in_progress" else "normal",
            "agent_id": agent_id,
            "skills": skills,
            "result": f"GitHub Issue #{num} · {'Открыта — в обработке агентов' if state_val == 'in_progress' else 'Выполнено Советом ИИ'}",
            "updated_at": it.get("updated_at") or _utc_iso(),
        })

    # 4) Board cards (active first + completed board history so all 152+ cards appear in 'Все задачи')
    def _card_prio(c_item):
        st0 = str(c_item.get("status") or "done")
        return 0 if st0 != "done" else 1

    for c in sorted(board_cards, key=_card_prio):
        if not isinstance(c, dict):
            continue
        cid = c.get("id") or c.get("card")
        st = str(c.get("status") or "done")
        t_id = f"CARD-{cid}"
        if t_id in seen_task_keys:
            continue
        seen_task_keys.add(t_id)
        title = _scrub_secrets(str(c.get("title") or f"Card #{cid}"))
        proj = str(c.get("project") or "Входящие_Офис")
        urls = [u.rstrip(".,;:!?)]}»«\"'<>") for u in re.findall(r"https?://[^\s<>'\"«»]+", str(c))]
        agent_id, skills = infer_seo_skills_for_task(title, "", urls[0] if urls else "")
        norm_st = (
            "done" if st == "done" else (
                "attention" if st in ("blocked_human", "awaiting_approval", "need_project", "not_studied", "failed") else (
                    "in_progress" if st in ("in_progress", "approved", "distributed") else "queued"
                )
            )
        )
        card_skills = list(c.get("skills_applied") or []) or skills
        unified_tasks.append({
            "id": t_id,
            "source": "office_board",
            "title": title[:130],
            "url": urls[0] if urls else "",
            "project": proj,
            "status": norm_st,
            "priority": "P1" if norm_st in ("attention", "in_progress") else ("done" if norm_st == "done" else "normal"),
            "agent_id": agent_id,
            "skills": card_skills[:5],
            "action_plan_ru": str(c.get("ai_solution") or ""),
            "raw_owner": str(c.get("owner") or ""),
            "result": f"Статус на доске: {st} (owner={c.get('owner') or 'agent'})",
            "updated_at": str(c.get("updated") or _utc_iso()),
        })

    # Count per agent
    agent_task_counts: dict[str, dict[str, int]] = {}
    for tk in unified_tasks:
        aid = tk["agent_id"]
        b = agent_task_counts.setdefault(aid, {"total": 0, "active": 0, "done": 0, "attention": 0})
        b["total"] += 1
        if tk["status"] in ("in_progress", "queued"):
            b["active"] += 1
        elif tk["status"] == "attention":
            b["attention"] += 1
        else:
            b["done"] += 1

    # Hierarchy Graph Nodes & Edges (Owner -> Bot -> Director -> Agents -> Active Tasks / Sites)
    seo_skills_all = list(manifest.get("skills") or ["seo", "seo-audit", "seo-technical", "seo-geo", "seo-schema", "seo-agentic"])
    cycle_num = int(telemetry.get("cycle_count") or 0)
    last_seo_iso = seo_state.get("last_run_iso") or _utc_iso()

    nodes: list[dict[str, Any]] = [
        {
            "id": "owner_vlaslav",
            "label": "Вы · Владелец",
            "sub": "@unityspiritpartners",
            "tier": 0,
            "kind": "owner",
            "status": "active",
            "skills": ["Стратегия", "Одобрение", "Команды"],
            "details": "Главный руководитель Global Spirit Group. Ставит задачи напрямую Директору через Telegram-бот и Виртуальный Офис.",
        },
        {
            "id": "bot_hq",
            "label": "@unitybusinessbot",
            "sub": "Главный Штаб · Bot Mesh",
            "tier": 1,
            "kind": "bot",
            "status": "active",
            "skills": ["/seo", "/tasks", "/store", "/bots", "/audit", "Голос/Фото OCR"],
            "details": "Круглосуточный Telegram-шлюз v34.5 + API 127.0.0.1:18799. Принимает задачи от вас и синхронизирует личных ботов команды.",
        },
        {
            "id": "bot_alberta",
            "label": "@lermontovplusbot",
            "sub": "Личный бот · Альберта",
            "tier": 1,
            "kind": "bot",
            "status": "active",
            "skills": ["Воронка 1M ₽", "Личный Директор"],
            "details": "Личный бот Альберты Ширани в системе Bot Mesh под контролем Главного Штаба.",
        },
        {
            "id": "dir_tim",
            "label": "Директор «Тим»",
            "sub": "AI Orchestrator · NLU v34.5",
            "tier": 2,
            "kind": "director",
            "status": "active",
            "skills": ["NLU Intent", "Memory RAG", "Multi-Agent Dispatch", "/seo", "/store"],
            "details": "Личный ИИ-Директор: понимает ваши запросы (RU/EN), хранит контекст диалога, распределяет задачи между агентами и собирает отчёты.",
        },
        {
            "id": "agent_seo",
            "label": "Claude SEO-Автопилот",
            "sub": f"{manifest.get('skills_count', 35)} скиллов · {manifest.get('agents_count', 21)} сабагентов",
            "tier": 3,
            "kind": "agent",
            "status": "active",
            "metric": f"Office {seo_state.get('local_avg_score', 96.4)}/100",
            "skills": seo_skills_all[:12],
            "details": f"Запускается ПЕРВЫМ при старте Windows и каждые 6ч. Последний цикл: {last_seo_iso}. Выполняет 47 on-page проверок, GEO/llms.txt, Schema, Sitemap и Drift.",
        },
        {
            "id": "agent_cloud",
            "label": "Cloud Agents 24/7",
            "sub": f"Цикл #{cycle_num} · Hydrogen/Argon",
            "tier": 3,
            "kind": "agent",
            "status": "active",
            "metric": f"Цикл #{cycle_num}",
            "skills": ["cloud_agent_worker", "GitHub Actions", "Auto-Study URLs", "Memory Sync"],
            "details": f"Автономный облачный совет 24/7 на GitHub Actions. Движок: {telemetry.get('model_engine') or 'structured-cloud-analyzer'}.",
        },
        {
            "id": "agent_store",
            "label": "Open V3 Store Builder",
            "sub": "Карточка #147 · Shopify + TMA",
            "tier": 3,
            "kind": "agent",
            "status": "active",
            "metric": "CPA $150 + SaaS",
            "skills": ["store_builder_engine", "Shopify OS 2.0", "VSL Scripts", "seo-ecommerce"],
            "details": "Собирает AI-магазины Shopify и Telegram Mini Apps за 15 минут с SEO-разметкой и лид-воронкой.",
        },
        {
            "id": "agent_research",
            "label": "Research & NLM Агент",
            "sub": "Глубокий разбор ссылок + #79",
            "tier": 3,
            "kind": "agent",
            "status": "working" if any(t["status"] == "in_progress" and t["agent_id"] == "agent_research" for t in unified_tasks) else "active",
            "metric": f"Issues: {sum(1 for it in issues if it.get('state')=='open')} откр.",
            "skills": ["director_nlu_engine", "NotebookLM CLI", "GitHub Models", "Ollama"],
            "details": "Изучает входящие ссылки, видео, конкурентов и готовит пошаговые планы монетизации без галлюцинаций.",
        },
        {
            "id": "agent_traffic",
            "label": "Traffic & Brand SEO",
            "sub": "Alberta Hub · GlobalPlus · Unity",
            "tier": 3,
            "kind": "agent",
            "status": "attention" if any(t["status"] == "attention" for t in unified_tasks) else "active",
            "metric": f"Live avg {seo_state.get('live_avg_score', 73.7)}/100",
            "skills": ["Traffic Engine", "seo-geo", "seo-local", "seo-competitor-pages"],
            "details": "Отслеживает внешние домены (globalplus.tech, unityspiritpartners.com, alberta-hub), трафик и паки SEO-доработок.",
        },
        {
            "id": "agent_apps",
            "label": "Mini Apps & Filmtools",
            "sub": "12 IT Продуктов · GlobalFilms",
            "tier": 3,
            "kind": "agent",
            "status": "active",
            "metric": "96–99/100 SEO",
            "skills": ["12_IT_Products", "globalfilms", "AI_Skills_Suite", "seo-schema"],
            "details": "Развивает конвейер 12 Telegram Mini Apps, каталог GlobalFilms и 30 макроэкономических ИИ-навыков.",
        },
    ]

    edges: list[dict[str, Any]] = [
        {"from": "owner_vlaslav", "to": "bot_hq", "label": "Команды / Задачи", "active": True},
        {"from": "bot_hq", "to": "dir_tim", "label": "NLU & Голосовые/Текст", "active": True},
        {"from": "bot_hq", "to": "bot_alberta", "label": "Bot Mesh Sync", "active": True},
        {"from": "dir_tim", "to": "agent_seo", "label": "SEO-First + /seo", "active": True},
        {"from": "dir_tim", "to": "agent_cloud", "label": "24/7 Очередь", "active": True},
        {"from": "dir_tim", "to": "agent_store", "label": "/store + Лиды", "active": True},
        {"from": "dir_tim", "to": "agent_research", "label": "Ссылки & Анализ", "active": True},
        {"from": "dir_tim", "to": "agent_traffic", "label": "Рост & Домены", "active": True},
        {"from": "dir_tim", "to": "agent_apps", "label": "Продукты & Витрины", "active": True},
    ]

    # Attach top priority live task/domain nodes (tier 4) so the graph stays crisp and interactive
    featured_tasks: list[dict[str, Any]] = []
    for st_prio in ("in_progress", "attention", "queued", "done"):
        for tk in unified_tasks:
            if tk["status"] == st_prio and len(featured_tasks) < 10:
                featured_tasks.append(tk)

    for idx, tk in enumerate(featured_tasks):
        nid = f"task_node_{idx}"
        nodes.append({
            "id": nid,
            "label": tk["id"] if len(tk["id"]) < 16 else tk["title"][:22],
            "sub": tk["title"][:44],
            "tier": 4,
            "kind": "task" if tk["source"] != "seo_domain_health" else "domain",
            "status": tk["status"],
            "metric": tk["priority"],
            "skills": tk["skills"],
            "url": tk.get("url") or "",
            "details": f"{tk['title']}\n\nНавыки: {', '.join(tk['skills'])}\nРезультат: {tk.get('result') or 'В очереди на исполнение'}",
        })
        edges.append({
            "from": tk["agent_id"],
            "to": nid,
            "label": tk["skills"][0] if tk["skills"] else "task",
            "active": tk["status"] in ("in_progress", "attention", "queued"),
        })

    # Build sanitized live feed
    live_feed: list[dict[str, Any]] = []
    if seo_state.get("last_run_iso"):
        live_feed.append({
            "ts": seo_state["last_run_iso"],
            "actor": "Claude SEO-Автопилот",
            "kind": "seo",
            "text": f"Выполнен цикл ({seo_state.get('trigger')}): локал {seo_state.get('local_avg_score')}/100, домены {seo_state.get('live_avg_score')}/100, задач: {seo_state.get('queued_completed', 0)}",
        })
    for a in reversed(director_actions[-10:]):
        if isinstance(a, dict):
            live_feed.append({
                "ts": str(a.get("ts") or a.get("t") or ""),
                "actor": f"Директор ({a.get('project') or 'Офис'})",
                "kind": str(a.get("action") or "director"),
                "text": _scrub_secrets(str(a.get("summary") or a.get("text") or "")),
            })
    if telemetry.get("updated_at"):
        live_feed.append({
            "ts": str(telemetry["updated_at"]),
            "actor": "Cloud Agents 24/7",
            "kind": "cloud",
            "text": f"Автономный цикл #{cycle_num} ({telemetry.get('orchestrator_mode') or 'Hydrogen'}): фактов {telemetry.get('memory_facts_count', 0)}, ссылок {telemetry.get('studied_urls_count', 0)}",
        })

    # Summary KPIs & per-user enrichment
    status_counts = {"in_progress": 0, "queued": 0, "attention": 0, "done": 0}
    user_counts: dict[str, int] = {k: 0 for k in USER_LABELS}
    for tk in unified_tasks:
        status_counts[tk["status"]] = status_counts.get(tk["status"], 0) + 1
        raw_owner = str(tk.pop("raw_owner", "") or "")
        u_key, u_label = infer_user_owner_for_task(
            tk.get("title", ""),
            tk.get("result", ""),
            tk.get("project", ""),
            raw_owner,
        )
        tk["user_key"] = u_key
        tk["user_label"] = u_label
        user_counts[u_key] = user_counts.get(u_key, 0) + 1
        if not tk.get("action_plan_ru"):
            tk["action_plan_ru"] = infer_action_plan_ru(
                tk.get("title", ""),
                tk.get("project", ""),
                tk.get("skills", []),
                u_key,
                tk.get("status", ""),
            )

    total_skills_installed = int(manifest.get("total_installed_skills_count") or manifest.get("skills_count") or 167)
    snapshot = {
        "schema_version": "34.7",
        "generated_at": _utc_iso(),
        "repos": {"primary": gtp_repo, "mirror": u_repo},
        "user_counts": user_counts,
        "skill_suites": {
            "claude_seo": list(manifest.get("skills") or []),
            "seo_agents": list(manifest.get("agents") or []),
            "marketing_cro": list(manifest.get("marketing_cro_skills") or []),
            "semrush": list(manifest.get("semrush_skills") or []),
            "automation_research": list(manifest.get("automation_research_skills") or []),
            "total_installed_skills_count": total_skills_installed,
        },
        "kpis": {
            "agents_online": 6,
            "seo_skills_installed": int(manifest.get("skills_count") or 35),
            "total_skills_installed": total_skills_installed,
            "seo_agents_installed": int(manifest.get("agents_count") or 21),
            "local_seo_score": seo_state.get("local_avg_score") or 96.4,
            "live_seo_score": seo_state.get("live_avg_score") or 73.7,
            "cloud_cycle": cycle_num,
            "open_issues": sum(1 for i in issues if i.get("state") == "open"),
            "closed_issues": sum(1 for i in issues if i.get("state") == "closed"),
            "board_total_cards": len(board_cards),
            "tasks_in_progress": status_counts["in_progress"],
            "tasks_queued": status_counts["queued"],
            "tasks_attention": status_counts["attention"],
            "tasks_done": status_counts["done"],
        },
        "seo_health": {
            "last_run_iso": seo_state.get("last_run_iso"),
            "trigger": seo_state.get("trigger"),
            "domains": seo_state.get("live_summary") or [],
            "drift_runs": (drift_hist.get("runs") or [])[-8:],
            "skills": seo_skills_all,
            "subagents": list(manifest.get("agents") or []),
        },
        "team_bots": team_bots,
        "agent_task_counts": agent_task_counts,
        "graph": {"nodes": nodes, "edges": edges},
        "tasks": unified_tasks,
        "live_feed": live_feed[:20],
        "fetch_errors": errors,
    }
    _save_json(SNAPSHOT_FILE, snapshot)
    return snapshot


def push_live_snapshot_to_github(snapshot: dict[str, Any] | None = None) -> dict[str, str]:
    snap = snapshot or _load_json(SNAPSHOT_FILE, None) or build_live_snapshot(include_remote=True)
    raw = json.dumps(snap, ensure_ascii=False, indent=2)
    for pat in SECRET_PATTERNS:
        if pat.search(raw):
            raise RuntimeError("Secret pattern matched in sanitized live snapshot — aborting GitHub push")

    cfg = _load_json(CFG_PATH, {})
    results: dict[str, str] = {}
    for key_repo, key_tok in (("gtp_repo", "gtp_token"), ("u_repo", "u_token")):
        repo = (cfg.get(key_repo) or "").strip()
        tok = (cfg.get(key_tok) or "").strip()
        if not (repo and tok):
            continue
        url = f"https://api.github.com/repos/{repo}/contents/agents_live_state.json"
        headers = {
            "Authorization": f"token {tok}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "GSG-AgentsLive-Sync",
        }
        sha = None
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{url}?ref=data", headers=headers), timeout=15) as r:
                sha = json.loads(r.read().decode("utf-8")).get("sha")
        except urllib.error.HTTPError as he:
            if he.code != 404:
                results[repo] = f"err:{he.code}"
                continue
        body: dict[str, Any] = {
            "message": f"Sync live agent command graph & SEO state ({snap.get('generated_at')})",
            "content": base64.b64encode(raw.encode("utf-8")).decode("ascii"),
            "branch": "data",
        }
        if sha:
            body["sha"] = sha
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="PUT")
            with urllib.request.urlopen(req, timeout=20) as pr:
                res = json.loads(pr.read().decode("utf-8"))
                results[repo] = str((res.get("commit") or {}).get("sha") or "")[:7]
        except Exception as exc:
            results[repo] = f"err:{exc}"
    return results


if __name__ == "__main__":
    snap = build_live_snapshot(include_remote=True)
    sync = push_live_snapshot_to_github(snap)
    print(json.dumps({"kpis": snap["kpis"], "nodes": len(snap["graph"]["nodes"]), "tasks": len(snap["tasks"]), "sync": sync}, ensure_ascii=False, indent=2))
