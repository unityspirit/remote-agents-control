# -*- coding: utf-8 -*-
"""
Global Spirit Agency — Autonomous Multi-Agent Agency Orchestrator (2026)
========================================================================
Integrates the complete agency business model into GSG Virtual Office:
- 7 Core Autonomous Agents:
  1. Директор Тим (CEO & Orchestrator)
  2. Аналитик & Скаут (B2B Lead Scout & Competitive Intelligence)
  3. B2B Closer & Teardown Specialist (JoinTECH 40-Min Audit & Offer Architect)
  4. Performance & Funnel Lead (GRIC Performance Marketing & Web Funnel)
  5. Content Factory Producer (Фермы Контента / Sazhnev Studio Video Lead)
  6. Lead Fullstack & TMA Architect (Telegram Mini Apps & Open V3 Store Builder)
  7. Cyber & Infrastructure Sentinel (Sysadmin Future & Habr 2026 VPN)
- High-ticket B2B Audits ($1,000–$3,500 / 50 000 – 250 000 ₽)
- 7-Day Video & Reels Sprints (7 scripts + Kling/Veo 3 prompts + HeyGen avatars)
- Integration with office_money_engine (port 18799) and tg_bot_poller.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

SYSTEM1_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = SYSTEM1_DIR.parent
CONTROL_PANEL_DIR = DOWNLOADS_DIR / "GitHub_Control_Panel"
AGENCY_HQ_DIR = Path(r"C:\Users\Vlaslav\Agency_HQ")
UNITY_INC_DIR = DOWNLOADS_DIR / "Unity Inc"

for p in (str(CONTROL_PANEL_DIR), str(SYSTEM1_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import store_builder_engine
except ImportError:
    store_builder_engine = None

try:
    import director_nlu_engine
except ImportError:
    director_nlu_engine = None


AGENCY_NAME = "Global Spirit Agency (Unity Inc Hub)"
AGENCY_VERSION = "2026.1-autonomous"

ROSTER_AGENTS: List[Dict[str, Any]] = [
    {
        "id": "director_tim",
        "name": "Директор Тим",
        "role": "CEO & Agency Orchestrator",
        "status": "active_24_7",
        "model": "gemini-flash-latest / hybrid-openrouter",
        "description": "Приём клиентских брифов, приоритизация задач, контроль дедлайнов и утверждение коммерческих предложений.",
        "skills": ["team-agent-orchestration", "council", "strategic-compact", "quality-gate"]
    },
    {
        "id": "b2b_scout",
        "name": "Аналитик & Скаут",
        "role": "B2B Lead Hunter & Competitor Scout",
        "status": "ready",
        "model": "deepseek-v3 / gemini-flash",
        "description": "Поиск ЛПР, парсинг сайтов конкурентов, выявление узких мест в воронках и стоимости лида (CPL ≤ $30).",
        "skills": ["research-ops", "competitors", "prospecting", "lead-intelligence"]
    },
    {
        "id": "closer_jointech",
        "name": "B2B Closer & Teardown Specialist",
        "role": "JoinTECH 40-Min Audit & Offer Architect",
        "status": "ready",
        "model": "claude-3-5-sonnet / gemini-pro",
        "description": "Проведение 180-секундного экспресс-разбора документов клиента, расчёт упущенной прибыли и продажа внедрений $1,000–$3,500.",
        "skills": ["master-agreement-generator", "copywriting", "sales-enablement", "pricing"]
    },
    {
        "id": "growth_gric",
        "name": "Performance & Funnel Lead",
        "role": "GRIC Web & Traffic Engineer",
        "status": "ready",
        "model": "gemini-flash / open_weight",
        "description": "Создание посадочных страниц с конверсией 12–25%, настройка Google/FB/Telegram рекламы по модели Pay-On-Results.",
        "skills": ["seo-page", "cro", "ads", "frontend-patterns"]
    },
    {
        "id": "content_factory",
        "name": "Content Factory Producer",
        "role": "True1 / Sazhnev Studio Video Lead",
        "status": "ready",
        "model": "veo-3 / kling-v1.6 / heygen / elevenlabs",
        "description": "Пакетное производство 75 Shorts/Reels в неделю, генерация AI-аватаров для клиник и недвижимости Белграда.",
        "skills": ["hyperframes", "hypit", "video", "content-engine"]
    },
    {
        "id": "tma_developer",
        "name": "Lead Fullstack & TMA Architect",
        "role": "Telegram Mini Apps & Store Builder",
        "status": "ready",
        "model": "claude-code / qwen2.5-coder",
        "description": "Сборка Telegram Mini App витрин (Open V3 Store), интеграция N8N сценариев, Nango CRM и платежных шлюзов.",
        "skills": ["feature-dev", "api-connector-builder", "vite-patterns", "build-fix"]
    },
    {
        "id": "cyber_sentinel",
        "name": "Cyber Defense Sentinel",
        "role": "Sysadmin Future & Habr 2026 VPN",
        "status": "ready",
        "model": "sysadmin-core / bash-mesh",
        "description": "Обезличивание данных клиентов перед отправкой в LLM, развертывание непробиваемого каскада VPN (Habr #1103460).",
        "skills": ["security-review", "network-troubleshooter", "docker-patterns"]
    }
]


SERVICES_CATALOG = [
    {
        "id": "b2b_ai_audit",
        "title": "B2B AI-Экспресс Аудит (180 сек) & Дорожная Карта",
        "price_usd": 750,
        "price_rub": 75000,
        "terms": "Входит в стоимость внедрения (0 ₽ при покупке пакета)",
        "deliverables": [
            "Аудит 1 реального регламента/договора заказчика за 180 сек",
            "Расчёт упущенной выгоды (дыра в 35% клиентов)",
            "6-узловая архитектура автоматизации с защитой коммерческой тайны"
        ]
    },
    {
        "id": "content_farm_sprint",
        "title": "Ферма Контента (75 Reels / Shorts за 7 дней)",
        "price_usd": 1200,
        "price_rub": 120000,
        "terms": "Пакет под ключ с AI-диктором (HeyGen + ElevenLabs)",
        "deliverables": [
            "75 смонтированных вертикальных видео с субтитрами",
            "3 языка дубляжа (RU / EN / SRB) для зарубежных рынков",
            "Промпты для Kling / Veo 3 / Runway для каждого ролика"
        ]
    },
    {
        "id": "tma_navigator",
        "title": "Telegram Mini App «Навигатор + Умная База Знаний»",
        "price_usd": 350,
        "price_rub": 29000,
        "mrr_rub": 1490,
        "terms": "Разовая установка + ежемесячная подписка на хостинг/обновления",
        "deliverables": [
            "Каталог постов и услуг прямо в Telegram",
            "Встроенный семантический AI-поиск по всей базе знаний",
            "Прямая передача лидов в CRM владельца с моментальным уведомлением"
        ]
    },
    {
        "id": "turnkey_ai_agency_system",
        "title": "Внедрение AI-Инфраструктуры Под Ключ ($1,500–$3,500)",
        "price_usd": 2500,
        "price_rub": 250000,
        "terms": "Pay-On-Results опция при достижении целевого CPL ≤ $30",
        "deliverables": [
            "Telegram Mini App + Веб-Витрина (Open V3)",
            "AI Голосовой Ресепшен (ночные звонки и запись лидов)",
            "Суверенный VPN-каскад и локальная защита данных (Sysadmin Future)"
        ]
    }
]


def get_agency_manifest() -> Dict[str, Any]:
    """Return high-level agency telemetry and status."""
    return {
        "ok": True,
        "agency_name": AGENCY_NAME,
        "version": AGENCY_VERSION,
        "status": "OPERATIONAL_24_7",
        "founders": ["@unityspiritpartners (Vlaslove)", "@mshlomon (Partner)"],
        "total_agents": len(ROSTER_AGENTS),
        "agents": ROSTER_AGENTS,
        "services": SERVICES_CATALOG,
        "local_api_port": 18799,
        "pillars": [
            "Pillar 1: High-ticket B2B AI Consulting & Audits ($1,000–$3,500)",
            "Pillar 2: Content Automation Factory (75 reels in 7 days, HeyGen/Kling)",
            "Pillar 3: Telegram Mini Apps & MRR Subscriptions (JoinTECH model)",
            "Pillar 4: Sovereign Infrastructure & Habr 2026 Unbreakable VPN"
        ],
        "timestamp_iso": datetime.now(timezone.utc).isoformat()
    }


def audit_b2b_client(client_name: str, niche: str, website_or_doc: str = "") -> Dict[str, Any]:
    """Execute JoinTECH 180-second teardown and generate agency audit."""
    c_clean = client_name.strip() or "Клиент B2B"
    n_clean = niche.strip() or "Услуги / Ритейл / Клиника"
    doc_clean = website_or_doc.strip() or "Корпоративный сайт и регламент обработки входящих лидов"

    leaks = [
        {
            "leak": "Дыра в удержании 35% клиентов после первого контакта",
            "impact": "Администраторы не перезванивают через 48 часов, лиды остывают и уходят к конкурентам.",
            "fix": "Telegram/WhatsApp триггерный бот с персонализированным пушем и предложением бонуса на повторный визит."
        },
        {
            "leak": "Ручная обработка типовых вопросов (потеря 4–6 часов ежедневно)",
            "impact": "Менеджеры тратят рабочее время на шаблонные консультации вместо закрытия ключевых сделок.",
            "fix": "Внедрение интеллектуального AI-консультанта на базе Базы Знаний компании с точностью ответов 98%."
        },
        {
            "leak": "Отсутствие единой витрины в Telegram (потеря мобильного трафика)",
            "impact": "Пользователи со смартфонов не хотят заполнять длинные формы на медленных веб-сайтах.",
            "fix": "Запуск Telegram Mini App с открытием за 0.3 секунды и заказом в 2 клика прямо в мессенджере."
        }
    ]

    blueprint_nodes = [
        {"node": 1, "title": "Входящий трафик (Reels/SEO/Аутрич)", "role": "Генерация целевых переходов с CPL ≤ $30"},
        {"node": 2, "title": "Telegram Mini App / Open V3 Витрина", "role": "Мгновенная мобильная квалификация лида за 30 сек"},
        {"node": 3, "title": "Умный AI-Ассистент (Zero-Leak RAG)", "role": "Ответы на сложные вопросы без утечки данных в OpenAI"},
        {"node": 4, "title": "Интеграция CRM (N8N / Webhook)", "role": "Автоматическая передача контакта в воронку ЛПР"},
        {"node": 5, "title": "Голосовой AI-Ресепшен (Ночные звонки)", "role": "Приём звонков 24/7 и запись в календарь"},
        {"node": 6, "title": "Аналитический Дашборд Руководителя", "role": "Ежедневная сводка выручки, конверсий и ROI в Telegram"}
    ]

    estimated_roi = {
        "monthly_saved_hours": 120,
        "revenue_growth_percent": "25–40%",
        "payback_period_days": 18,
        "recommended_package": "Внедрение AI-Инфраструктуры Под Ключ ($1,500–$2,500)"
    }

    report_md = f"""# 📑 ЭКСПРЕСС-АУДИТ АВТОМАТИЗАЦИИ ДЛЯ: {c_clean}
**Ниша:** `{n_clean}` | **Анализировалось:** `{doc_clean[:100]}`
**Аудитор:** Global Spirit Agency (Директор Тим & B2B Closer)
**Дата:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

---

## 1. 🔍 ТОП-3 УЗКИХ МЕСТА И УТЕЧКИ ВЫРУЧКИ
1. **{leaks[0]['leak']}**
   - *Проблема:* {leaks[0]['impact']}
   - *Решение:* {leaks[0]['fix']}
2. **{leaks[1]['leak']}**
   - *Проблема:* {leaks[1]['impact']}
   - *Решение:* {leaks[1]['fix']}
3. **{leaks[2]['leak']}**
   - *Проблема:* {leaks[2]['impact']}
   - *Решение:* {leaks[2]['fix']}

---

## 2. 🏛️ 6-УЗЛОВАЯ ЦЕЛЕВАЯ АРХИТЕКТУРА ВНЕДРЕНИЯ
"""
    for n in blueprint_nodes:
        report_md += f"- **Узел {n['node']}: {n['title']}** — {n['role']}\n"

    report_md += f"""
---

## 3. 📊 РАСЧЁТ ЭКОНОМИЧЕСКОГО ЭФФЕКТА
- **Экономия времени команды:** ~{estimated_roi['monthly_saved_hours']} часов/мес.
- **Ожидаемый рост конверсии и выручки:** {estimated_roi['revenue_growth_percent']}.
- **Срок окупаемости инвестиций:** {estimated_roi['payback_period_days']} дней.
- **Рекомендуемый пакет внедрения:** {estimated_roi['recommended_package']}.
"""

    return {
        "ok": True,
        "client_name": c_clean,
        "niche": n_clean,
        "leaks": leaks,
        "blueprint_nodes": blueprint_nodes,
        "estimated_roi": estimated_roi,
        "report_markdown": report_md
    }


def generate_content_sprint(topic: str, target_audience: str = "B2B клиенты и предприниматели", language: str = "ru") -> Dict[str, Any]:
    """Generate 7-day video & reels sprint for Content Factory."""
    t_clean = topic.strip() or "Внедрение AI и рост продаж без раздувания штата"
    aud_clean = target_audience.strip()

    days = []
    titles = [
        "День 1: Разбор главной боли: почему 80% компаний сливают бюджет на AI",
        "День 2: 180 секунд: как автоматизировать договор без найма юриста",
        "День 3: Демонстрация Telegram Mini App: конверсия в 3 раза выше сайта",
        "День 4: Голосовой робот, который не бесит клиентов: ночной тест-драйв",
        "День 5: Кейс из практики: возврат 35% остывших лидов из старой базы",
        "День 6: Безопасность: как не слить клиентскую базу в публичные нейросети",
        "День 7: Оффер недели: бесплатный аудит 1 процесса вашей компании за 40 минут"
    ]

    for i, title in enumerate(titles, 1):
        days.append({
            "day": i,
            "title": title,
            "hook": f"«Если вы всё ещё платите за это сотрудникам в 2026 году — вы теряете миллионы...»",
            "script_30s": (
                f"Хук: {title}. "
                f"Проблема: клиенты ждут ответа часами, а менеджеры перегружены рутиной. "
                f"Решение: наш агент за 3 секунды квалифицирует лид и отправляет готовое КП. "
                f"Призыв: Напишите 'АУДИТ' в комментариях или перейдите по ссылке в шапке профиля!"
            ),
            "video_prompt_veo_kling": (
                f"Cinematic close-up of a high-tech modern office desk, sleek smartphone showing Telegram Mini App interface, "
                f"glowing holographic UI nodes, smooth 4k 60fps camera dolly forward, photorealistic, professional lighting"
            ),
            "heygen_avatar_prompt": f"Professional executive in dark blazer, confident posture, friendly tone, speaking {language.upper()} fluently.",
            "carousel_slide": f"Слайд {i}: {title[:40]} | 1 главная цифра | Ссылка на Telegram Mini App"
        })

    return {
        "ok": True,
        "topic": t_clean,
        "target_audience": aud_clean,
        "language": language,
        "sprint_days": days,
        "production_status": "READY_FOR_RENDER"
    }


def sync_agency_lead_to_office(client_name: str, niche: str, service_id: str, contact: str = "") -> Dict[str, Any]:
    """Register client project/lead into board.json and notify office."""
    board_path = DOWNLOADS_DIR / "Входящие_Офис" / "_system" / "board.json"
    card_id = f"agency-{int(time.time())}"

    new_card = {
        "id": card_id,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "title": f"🏢 [AGENCY] {client_name} ({niche}) — Услуга: {service_id}",
        "project": "Unity Inc",
        "status": "in_progress",
        "owner": "director_tim",
        "assignee": "closer_jointech",
        "kind": "agency_lead",
        "contact": contact,
        "next": {
            "who": "closer_jointech",
            "what": "Провести 40-минутный стратегический звонок по чек-листу JoinTECH"
        }
    }

    if board_path.exists():
        try:
            with open(board_path, "r", encoding="utf-8") as f:
                b = json.load(f)
            cards = b.get("cards", {})
            if isinstance(cards, dict):
                cards[card_id] = new_card
                b["cards"] = cards
                with open(board_path, "w", encoding="utf-8") as f:
                    json.dump(b, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {"ok": True, "card_id": card_id, "card": new_card}
