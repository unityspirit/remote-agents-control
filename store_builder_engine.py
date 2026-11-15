# -*- coding: utf-8 -*-
"""
Open V3 AI Store & Telegram Mini App Builder Engine (Card #147 + #157)
=======================================================================
Builds end-to-end Shopify OS 2.0 + Standalone Web + Telegram Mini App storefronts,
persists client lead + revenue pipeline records into Virtual Office CRM,
and generates ready-to-launch traffic/outreach assets.

Zero hardcoded secrets: all credentials are loaded dynamically from
.tg_daemon_config.json / environment / local .env files.
"""

from __future__ import annotations

import base64
import csv
import datetime
import html as _html
import io
import json
import os
import re
import secrets
import string
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / ".tg_daemon_config.json"
DOWNLOADS_DIR = BASE_DIR.parent
PRODUCTS_OUTPUT_ROOT = DOWNLOADS_DIR / "12 IT Продуктов (Короче Капитан)" / "generated_stores"
LEADS_OUTPUT_DIR = DOWNLOADS_DIR / "Входящие_Офис" / "Выручка_и_Лиды"
LEADS_JSONL_PATH = LEADS_OUTPUT_DIR / "store_leads_ledger.jsonl"
PIPELINE_MD_PATH = LEADS_OUTPUT_DIR / "STORE_PIPELINE_SUMMARY.md"

DEFAULT_SHOPIFY_TRIAL_URL = "https://www.shopify.com/free-trial"
OFFICE_LIVE_URL = "https://globalteampartners.github.io/remote-agents-control/"
OPEN_V3_WEB_URL = "https://globalteampartners.github.io/remote-agents-control/ai_store_builder/"

_ENV_FALLBACK_CANDIDATES = (
    BASE_DIR / ".env",
    DOWNLOADS_DIR / "Рязань" / "Страйк" / ".env",
    DOWNLOADS_DIR / "Virtual_Office" / ".office_secrets.json",
)


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _slugify(text: str, max_len: int = 38) -> str:
    translit = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    raw = (text or "ai-store").strip().lower()
    mapped = "".join(translit.get(ch, ch) for ch in raw)
    cleaned = re.sub(r"[^a-z0-9]+", "-", mapped).strip("-")
    if not cleaned:
        cleaned = "ai-store"
    return cleaned[:max_len].strip("-") or "ai-store"


def _load_runtime_config() -> Dict[str, Any]:
    """Load local daemon & API configuration without exposing secrets publicly."""
    cfg: Dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            cfg = {}

    if not cfg.get("gemini_api_key"):
        env_gem = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
        if env_gem:
            cfg["gemini_api_key"] = env_gem

    for cand in _ENV_FALLBACK_CANDIDATES:
        if cfg.get("gemini_api_key") and cfg.get("shopify_partner_url"):
            break
        if not cand.exists():
            continue
        try:
            if cand.suffix.lower() == ".json":
                obj = json.loads(cand.read_text(encoding="utf-8-sig"))
                if isinstance(obj, dict):
                    if not cfg.get("gemini_api_key") and obj.get("GEMINI_API_KEY"):
                        cfg["gemini_api_key"] = str(obj["GEMINI_API_KEY"]).strip()
                    if not cfg.get("shopify_partner_url") and obj.get("SHOPIFY_PARTNER_URL"):
                        cfg["shopify_partner_url"] = str(obj["SHOPIFY_PARTNER_URL"]).strip()
            else:
                for line in cand.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
                    s = line.strip()
                    if not s or s.startswith("#") or "=" not in s:
                        continue
                    k, v = s.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k == "GEMINI_API_KEY" and v and not cfg.get("gemini_api_key"):
                        cfg["gemini_api_key"] = v
                    elif k == "SHOPIFY_PARTNER_URL" and v and not cfg.get("shopify_partner_url"):
                        cfg["shopify_partner_url"] = v
        except Exception:
            continue

    return cfg


def _generate_store_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#%"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ---------------------------------------------------------------------------
# Curated Multi-Niche Blueprints (Offline / Ultra-Fast Fallback)
# ---------------------------------------------------------------------------
_NICHE_PRESETS: List[Dict[str, Any]] = [
    {
        "keys": ("beauty", "skin", "cosmetic", "glow", "космет", "уход", "красот", "сыворот"),
        "brand_ru": "AuraSkin AI Lab",
        "brand_en": "LumiDerm Lab",
        "palette": {"primary": "#0f172a", "accent": "#ec4899", "accent_soft": "#fdf2f8", "bg": "#0b0f19", "surface": "#131b2e", "text": "#f8fafc"},
        "tagline_ru": "Пептидный уход и сияние кожи с подбором по типу лица за 60 секунд",
        "tagline_en": "Clinical-grade peptide skincare engineered for visible radiance in 14 days",
        "products": [
            ("Peptide Barrier Serum 30ml", "Восстанавливающая пептидная сыворотка 30мл", "Serums", 49.0, 82.0, 11.5, "Best Seller"),
            ("Cryo-Peptide Eye Roller", "Крио-роллер с кофеином и пептидами вокруг глаз", "Eye Care", 36.0, 58.0, 8.2, "Viral Hit"),
            ("LED Microcurrent Glow Wand", "LED-массажёр микротоки 4-в-1 для лифтинга", "Devices", 89.0, 149.0, 21.0, "High Ticket"),
            ("Ceramide Cloud Cleanser", "Увлажняющая пенка с церамидами pH 5.5", "Cleansers", 28.0, 44.0, 6.5, "Daily Must"),
            ("SPF50 Invisible Silk Fluid", "Невидимый солнцезащитный флюид SPF50 без плёнки", "Sun Care", 34.0, 54.0, 7.8, "Clean Beauty"),
            ("14-Day Glass Skin Vault Kit", "Полный набор «Фарфоровая кожа» на 14 дней + гайд", "Bundles", 119.0, 195.0, 29.0, "Max AOV"),
        ],
    },
    {
        "keys": ("tech", "gadget", "electron", "audio", "smart", "гаджет", "электрон", "умн", "науш"),
        "brand_ru": "VoltEdge Gear",
        "brand_en": "VoltEdge Studio",
        "palette": {"primary": "#090d16", "accent": "#38bdf8", "accent_soft": "#0c4a6e", "bg": "#080b12", "surface": "#111827", "text": "#f8fafc"},
        "tagline_ru": "Минималистичные смарт-гаджеты для продуктивности, авто и рабочего стола",
        "tagline_en": "Minimalist desk & travel tech engineered for creators and remote builders",
        "products": [
            ("MagSafe 3-in-1 Aluminum Dock", "Алюминиевая док-станция MagSafe 3-в-1 15W Fast", "Charging", 64.0, 99.0, 14.5, "Top Margin"),
            ("Studio ANC Wireless Earbuds", "Беспроводные наушники ANC 48dB с Hi-Res кейсом", "Audio", 79.0, 129.0, 18.0, "Best Seller"),
            ("100W GaN Pocket Charger Pro", "Сверхкомпактная зарядка GaN 100W на 4 порта", "Power", 48.0, 78.0, 11.2, "Travel Pick"),
            ("Magnetic Desk Light Bar AI", "Умная LED-лампа на монитор с автоподстройкой света", "Desk Setup", 54.0, 89.0, 13.0, "Viral"),
            ("USB-C 11-in-1 NVMe Hub", "Док-станция USB-C 11-в-1 с отсеком под SSD NVMe", "Workspace", 92.0, 145.0, 22.0, "Pro Choice"),
            ("Creator Desk Ultimate Bundle", "Полный сет рабочего места: GaN + Свет + MagSafe", "Bundles", 149.0, 239.0, 36.0, "High AOV"),
        ],
    },
    {
        "keys": ("fit", "gym", "sport", "health", "well", "спорт", "фитнес", "здоров", "бад", "витам"),
        "brand_ru": "ApexPulse Lab",
        "brand_en": "ApexPulse Athletics",
        "palette": {"primary": "#06120e", "accent": "#10b981", "accent_soft": "#064e3b", "bg": "#070f0c", "surface": "#111f1a", "text": "#f8fafc"},
        "tagline_ru": "Экипировка для быстрого восстановления, дома-зала и пиковой энергии",
        "tagline_en": "Precision recovery gear & smart fitness essentials for peak daily output",
        "products": [
            ("Pro Percussive Mini Massage Gun", "Тихий перкуссионный массажёр-пистолет 6 насадок", "Recovery", 69.0, 119.0, 16.0, "Best Seller"),
            ("Smart Jump Rope Calorie Counter", "Умная скакалка с LED-счётчиком калорий и Bluetooth", "Cardio", 32.0, 54.0, 7.0, "Impulse Buy"),
            ("GripPro Ceramic Shaker 700ml", "Спортивный термо-шейкер с керамическим покрытием", "Hydration", 27.0, 42.0, 5.8, "Daily Hit"),
            ("Electrolyte Focus Sticks (30x)", "Изотоник Focus+ с магнием и витаминами группы B", "Nutrition", 39.0, 59.0, 8.9, "Subscription"),
            ("Home Studio Resistance Set", "Набор тканевых эспандеров + якорь для двери PRO", "Strength", 45.0, 75.0, 10.5, "Starter"),
            ("Athlete Recovery Vault Kit", "Полный комплект восстановления: Массажёр + Магний + Ролл", "Bundles", 124.0, 199.0, 29.5, "Max Margin"),
        ],
    },
    {
        "keys": ("pet", "dog", "cat", "питом", "собак", "кош", "зоо"),
        "brand_ru": "PawNest Club",
        "brand_en": "PawNest Essentials",
        "palette": {"primary": "#161108", "accent": "#f59e0b", "accent_soft": "#78350f", "bg": "#0e0b07", "surface": "#1d1710", "text": "#fffbeb"},
        "tagline_ru": "Умные гаджеты и забота о питомцах без лишних хлопот дома и в поездках",
        "tagline_en": "Smart pet care gadgets loved by 40,000+ modern dog & cat parents",
        "products": [
            ("SteamBrush Shedding Remover", "Паровая щётка-расчёска анти-шерсть с мягким паром", "Grooming", 34.0, 59.0, 6.8, "TikTok Viral"),
            ("Smart Gravity Water Fountain 3L", "Умный фонтан-поилка 3L с фильтром и тихим насосом", "Feeding", 49.0, 84.0, 11.4, "Best Seller"),
            ("Orthopedic Cooling Pet Cloud Bed", "Ортопедический лежак-облако с терморегуляцией", "Comfort", 74.0, 125.0, 19.0, "High AOV"),
            ("Hands-Free LED Leash + Pouch", "Поводок через плечо с фонариком и дозатором пакетов", "Walk Gear", 38.0, 62.0, 8.1, "Travel"),
            ("Interactive Treat Puzzle Sphere", "Интерактивная кормушка-головоломка от скуки", "Toys", 29.0, 48.0, 6.2, "Reorder"),
            ("Happy Pet Starter Care Box", "Премиум-бокс: Фонтан 3L + Паровая щётка + Игрушка", "Bundles", 98.0, 165.0, 24.0, "Gift Hit"),
        ],
    },
    {
        "keys": ("fashion", "cloth", "wear", "hoodie", "dress", "одежд", "стиль", "бренд", "футбол"),
        "brand_ru": "NoirForm Atelier",
        "brand_en": "NoirForm Studio",
        "palette": {"primary": "#111116", "accent": "#a855f7", "accent_soft": "#4c1d95", "bg": "#09090d", "surface": "#171721", "text": "#f8fafc"},
        "tagline_ru": "Капсульный streetwear и базовая эстетика премиального кроя 460gsm",
        "tagline_en": "Heavyweight capsule streetwear cut from 460gsm organic cotton",
        "products": [
            ("Heavyweight 460gsm Boxy Hoodie", "Худи оверсайз плотностью 460г/м² с двойным капюшоном", "Hoodies", 78.0, 120.0, 18.5, "Signature"),
            ("Structured Merino Knit Polo", "Структурное поло из мериноса и органического хлопка", "Knitwear", 65.0, 98.0, 15.0, "Quiet Luxury"),
            ("Relaxed Pleated Tech Trousers", "Брюки свободного кроя со складками, несминаемая ткань", "Bottoms", 74.0, 115.0, 17.5, "Best Seller"),
            ("Supima Minimalist 2-Pack Tees", "Сет из 2 плотных футболок Supima 280г/м²", "Tees", 54.0, 85.0, 12.0, "Staple"),
            ("Crossbody Nylon Utility Bag", "Сумка-кроссбоди из баллистического нейлона водостойкая", "Accessories", 49.0, 79.0, 10.5, "Addon"),
            ("Capsule Weekend Wardrobe (3pc)", "Готовая капсула на выходные: Худи + Брюки + Сумка", "Bundles", 168.0, 265.0, 43.0, "VIP Bundle"),
        ],
    },
    {
        "keys": ("home", "kitchen", "decor", "coffee", "дом", "кухн", "интерьер", "кофе"),
        "brand_ru": "NordHearth Home",
        "brand_en": "NordHearth Living",
        "palette": {"primary": "#111418", "accent": "#f97316", "accent_soft": "#7c2d12", "bg": "#0b0d10", "surface": "#161b22", "text": "#f8fafc"},
        "tagline_ru": "Эстетика уюта, смарт-свет и премиальные аксессуары для дома и кофе-бара",
        "tagline_en": "Architectural home lighting & specialty coffee bar objects built to last",
        "products": [
            ("Cordless Brass Sunset Table Lamp", "Беспроводная настольная лампа Sunset из матовой латуни", "Lighting", 58.0, 95.0, 13.5, "Viral Decor"),
            ("Precision Pour-Over Kettle 0.9L", "Чайник для пуровера с контролем температуры до 1°C", "Coffee Bar", 76.0, 125.0, 18.0, "Barista Hit"),
            ("Amber Ceramic Pour-Over Carafe", "Янтарный керамический графин + воронка V60 ручной работы", "Tableware", 44.0, 72.0, 9.8, "Artisan"),
            ("Ultrasonic Cedar Wood Diffuser", "Ультразвуковой аромадиффузор из шпона кедра и стекла", "Aroma", 46.0, 76.0, 10.2, "Gift Choice"),
            ("Magnetic Oak Knife Dock Bar", "Магнитный держатель для ножей из массива дуба", "Kitchen", 52.0, 85.0, 11.5, "Clean Kitchen"),
            ("Morning Ritual Barista Pack", "Набор утреннего бариста: Чайник + Графин + Диффузор", "Bundles", 139.0, 225.0, 33.5, "Max AOV"),
        ],
    },
]


def _pick_preset(niche: str) -> Dict[str, Any]:
    low = (niche or "").lower()
    for p in _NICHE_PRESETS:
        if any(k in low for k in p["keys"]):
            return p
    return _NICHE_PRESETS[1]


def _call_store_llm(niche: str, brand_hint: str, language: str, cfg: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
    """Attempt live LLM JSON generation via director_nlu_engine router (Gemini -> Ollama)."""
    try:
        import director_nlu_engine
    except Exception:
        return None, "fallback"

    gem_key = cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or ""
    ollama_models = director_nlu_engine._get_ollama_models()
    if not gem_key and not ollama_models:
        return None, "curated_engine"

    lang_lbl = "Russian (with concise English SKU handle)" if language == "ru" else "English"
    prompt = (
        f"You are an 8-figure Shopify e-commerce & Telegram Mini App architect.\n"
        f"Create a complete, high-converting store blueprint in JSON ONLY for niche: '{niche}'.\n"
        f"Preferred brand hint: '{brand_hint or 'invent a memorable 2-word brand name'}'.\n"
        f"Copy language: {lang_lbl}.\n"
        "Return strict JSON matching this exact schema:\n"
        "{\n"
        '  "brand_name": "string",\n'
        '  "tagline": "short 1-sentence value prop",\n'
        '  "hero_headline": "punchy conversion headline",\n'
        '  "hero_subheadline": "trust-building subheadline",\n'
        '  "usp_list": ["4 short bullet benefits"],\n'
        '  "products": [\n'
        '    {"title": "string", "category": "string", "tagline": "1 line hook", "description": "2 concise persuasion sentences", "price_usd": 49, "compare_at_usd": 79, "cogs_usd": 12, "badge": "Best Seller"}\n'
        "  ]\n"
        "}\n"
        "Provide exactly 6 varied products (1 flagship gadget/item, 1 accessory, 1 consumable, 1 starter, 1 gift, 1 high-AOV bundle)."
    )
    ctx = {"gemini_api_key": gem_key, "gh_token": cfg.get("gtp_token") or ""}
    txt, engine_used, _err = director_nlu_engine.call_ai_router(
        ctx,
        [{"role": "system", "content": "Output valid JSON object only, no markdown fences."}, {"role": "user", "content": prompt}],
        fast=True,
        max_tokens=1500,
        temperature=0.35,
        timeout=20,
        json_mode=True,
    )
    if not txt:
        return None, "curated_engine"

    raw = txt.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if m:
        raw = m.group(0)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict) and isinstance(parsed.get("products"), list) and len(parsed["products"]) >= 3:
            return parsed, engine_used or "ai_live"
    except Exception:
        pass
    return None, "curated_engine"


def build_shopify_products_csv(spec: Dict[str, Any]) -> str:
    """Build valid Shopify Admin Product CSV import file matching official schema."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([
        "Handle", "Title", "Body (HTML)", "Vendor", "Product Category", "Type",
        "Tags", "Published", "Option1 Name", "Option1 Value", "Variant SKU",
        "Variant Grams", "Variant Inventory Tracker", "Variant Inventory Qty",
        "Variant Inventory Policy", "Variant Fulfillment Service",
        "Variant Price", "Variant Compare At Price", "Variant Requires Shipping",
        "Variant Taxable", "Cost per item", "Status"
    ])
    vendor = spec["brand_name"]
    use_rub = spec.get("currency", "USD").upper() == "RUB"
    for p in spec["products"]:
        price = p["price_rub"] if use_rub else p["price_usd"]
        cmp_price = p["compare_at_rub"] if use_rub else p["compare_at_usd"]
        cogs = round(p["cogs_usd"] * (95 if use_rub else 1), 2)
        body_html = f"<p><b>{_html.escape(p['tagline'])}</b></p><p>{_html.escape(p['description'])}</p><ul><li>Official Guarantee &amp; 24/7 Support</li><li>Tracked Fast Dispatch</li></ul>"
        writer.writerow([
            p["handle"], p["title"], body_html, vendor, spec["niche"], p["category"],
            f"ai-built, {p['category']}, {p['badge']}, open-v3", "TRUE",
            "Edition", "Standard", p["sku"], "320", "shopify", "100",
            "deny", "manual", f"{price:.2f}", f"{cmp_price:.2f}",
            "TRUE", "TRUE", f"{cogs:.2f}", "active"
        ])
    return buf.getvalue()


def build_shopify_os2_index_json(spec: Dict[str, Any]) -> str:
    """Generate Shopify Online Store 2.0 compatible templates/index.json."""
    theme_doc = {
        "sections": {
            "hero_banner": {
                "type": "image-banner",
                "settings": {
                    "heading": spec["hero_headline"],
                    "subtext": spec["hero_subheadline"],
                    "button_label": spec["cta_text"],
                    "promo_badge": f"{spec['promo_code']} · -15% FIRST ORDER",
                    "color_scheme": "inverse"
                }
            },
            "featured_collection": {
                "type": "featured-collection",
                "settings": {
                    "title": f"{spec['brand_name']} — Curated AI Bestsellers",
                    "products_to_show": len(spec["products"]),
                    "columns_desktop": 3,
                    "show_vendor": False,
                    "enable_quick_add": True
                }
            },
            "usp_multicolumn": {
                "type": "multicolumn",
                "settings": {
                    "title": "Why Customers Choose Us",
                    "columns_desktop": 4,
                    "items": spec["usp_list"]
                }
            },
            "trust_guarantee": {
                "type": "rich-text",
                "settings": {
                    "heading": "30-Day Money-Back Guarantee & Verified Delivery",
                    "text": spec["pages"]["shipping_returns"],
                    "cta_code": spec["promo_code"]
                }
            }
        },
        "order": ["hero_banner", "featured_collection", "usp_multicolumn", "trust_guarantee"],
        "generated_by": "Global Spirit Group Open V3 AI Store Builder v34.3",
        "brand_palette": spec["palette"]
    }
    return json.dumps(theme_doc, ensure_ascii=False, indent=2)


def build_telegram_mini_app_config(spec: Dict[str, Any]) -> str:
    """Generate deploy-ready Telegram Mini App catalog & payment spec."""
    tma = {
        "app_name": spec["brand_name"],
        "store_id": spec["store_id"],
        "slug": spec["slug"],
        "bot_username": "@unitybusinessbot",
        "currency": spec["currency"],
        "promo_code": spec["promo_code"],
        "payment_methods": ["telegram_stars", "yookassa", "prodamus", "stripe_shopify"],
        "theme": spec["palette"],
        "products": [
            {
                "sku": p["sku"],
                "title": p["title"],
                "category": p["category"],
                "price": p["price_rub"] if spec["currency"] == "RUB" else p["price_usd"],
                "compare_at": p["compare_at_rub"] if spec["currency"] == "RUB" else p["compare_at_usd"],
                "stars_price": max(50, int(round(p["price_usd"] * 50))),
                "badge": p["badge"],
                "tagline": p["tagline"],
            }
            for p in spec["products"]
        ],
        "webhook_sync": OPEN_V3_WEB_URL,
    }
    return json.dumps(tma, ensure_ascii=False, indent=2)


def _product_svg_data_uri(title: str, badge: str, accent: str, idx: int) -> str:
    icons = ["✨", "⚡", "🛡️", "💎", "🚀", "🎁"]
    em = icons[idx % len(icons)]
    safe_badge = _html.escape(badge[:18])
    safe_t = _html.escape(title[:22])
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 360 220'>"
        f"<defs><linearGradient id='g{idx}' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0%' stop-color='#0f172a'/><stop offset='100%' stop-color='{accent}' stop-opacity='0.35'/></linearGradient></defs>"
        f"<rect width='360' height='220' rx='18' fill='url(#g{idx})'/>"
        f"<circle cx='180' cy='96' r='44' fill='{accent}' fill-opacity='0.22' stroke='{accent}' stroke-width='2'/>"
        f"<text x='180' y='110' text-anchor='middle' font-size='38'>{em}</text>"
        f"<rect x='18' y='18' width='120' height='24' rx='12' fill='{accent}' fill-opacity='0.25'/>"
        f"<text x='78' y='34' text-anchor='middle' fill='#f8fafc' font-family='Arial' font-size='11' font-weight='bold'>{safe_badge}</text>"
        f"<text x='180' y='182' text-anchor='middle' fill='#e2e8f0' font-family='Arial' font-size='14' font-weight='bold'>{safe_t}</text>"
        f"</svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def build_standalone_storefront_html(spec: Dict[str, Any]) -> str:
    """Build self-contained responsive HTML5 storefront + Telegram WebApp checkout."""
    use_rub = spec.get("currency", "USD").upper() == "RUB"
    lang = spec.get("language", "ru")
    pal = spec["palette"]
    accent = pal.get("accent", "#38bdf8")

    products_js = []
    for i, p in enumerate(spec["products"]):
        price = p["price_rub"] if use_rub else p["price_usd"]
        cmp_p = p["compare_at_rub"] if use_rub else p["compare_at_usd"]
        products_js.append({
            "sku": p["sku"],
            "title": p["title"],
            "category": p["category"],
            "tagline": p["tagline"],
            "description": p["description"],
            "price": price,
            "compare_at": cmp_p,
            "badge": p["badge"],
            "img": _product_svg_data_uri(p["title"], p["badge"], accent, i),
        })

    curr_sym = "₽" if use_rub else "$"
    labels = {
        "ru": {
            "promo": f"🎁 Промокод {spec['promo_code']} даёт -15% на первый заказ · Бесплатная доставка",
            "cart": "Корзина",
            "all": "Все товары",
            "add": "В корзину",
            "empty": "Корзина пока пуста — добавьте товар для оформления заказа.",
            "coupon_ph": "Промокод (WELCOME15)",
            "apply": "Применить",
            "checkout": "Оформить заказ (Telegram / WhatsApp)",
            "total": "Итого",
            "order_sent": "Заказ сформирован! Передача в бот-менеджер и Telegram...",
        },
        "en": {
            "promo": f"🎁 Use code {spec['promo_code']} for 15% OFF your first order · Fast tracked shipping",
            "cart": "Cart",
            "all": "All Products",
            "add": "Add to Cart",
            "empty": "Your bag is empty — add items to checkout.",
            "coupon_ph": "Promo code (WELCOME15)",
            "apply": "Apply",
            "checkout": "Complete Order (Telegram / Instant)",
            "total": "Total",
            "order_sent": "Order created! Ready for payment & fulfillment.",
        },
    }[lang if lang in ("ru", "en") else "ru"]

    safe_brand = _html.escape(spec["brand_name"])
    safe_headline = _html.escape(spec["hero_headline"])
    safe_sub = _html.escape(spec["hero_subheadline"])
    safe_cta = _html.escape(spec["cta_text"])
    usp_html = "".join(f"<div class='usp-pill'>✅ {_html.escape(u)}</div>" for u in spec["usp_list"])

    return f"""<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no">
<title>{safe_brand} — Official Storefront</title>
<script src="https://telegram.org/js/telegram-web-app.js" async></script>
<style>
  :root {{ --bg:{pal.get('bg','#080b12')}; --surface:{pal.get('surface','#111827')}; --accent:{accent}; --text:{pal.get('text','#f8fafc')}; --muted:#94a3b8; --border:rgba(255,255,255,0.1); }}
  * {{ box-sizing:border-box; margin:0; padding:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  body {{ background:var(--bg); color:var(--text); line-height:1.5; padding-bottom:84px; }}
  .anno {{ background:linear-gradient(90deg,{accent},#6366f1); color:#fff; text-align:center; font-size:12px; font-weight:700; padding:8px 12px; }}
  header {{ position:sticky; top:0; z-index:30; background:rgba(8,11,18,0.92); backdrop-filter:blur(10px); border-bottom:1px solid var(--border); padding:12px 18px; display:flex; justify-content:space-between; align-items:center; }}
  .brand {{ font-size:18px; font-weight:900; letter-spacing:-0.02em; }}
  .cart-btn {{ background:rgba(255,255,255,0.08); border:1px solid var(--border); color:var(--text); padding:8px 14px; border-radius:10px; font-weight:700; cursor:pointer; font-size:13px; }}
  .wrap {{ max-width:1040px; margin:0 auto; padding:18px; }}
  .hero {{ background:linear-gradient(135deg, rgba(255,255,255,0.04), rgba(56,189,248,0.09)); border:1px solid var(--border); border-radius:20px; padding:26px 22px; margin-bottom:20px; }}
  .hero h1 {{ font-size:26px; font-weight:900; line-height:1.2; margin-bottom:10px; }}
  .hero p {{ color:var(--muted); font-size:14px; margin-bottom:16px; max-width:680px; }}
  .hero-cta {{ display:inline-block; background:var(--accent); color:#04101d; font-weight:900; border:none; border-radius:12px; padding:12px 22px; cursor:pointer; font-size:14px; }}
  .usps {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:18px; }}
  .usp-pill {{ background:var(--surface); border:1px solid var(--border); border-radius:999px; padding:6px 12px; font-size:12px; font-weight:600; }}
  .cats {{ display:flex; gap:8px; overflow-x:auto; margin-bottom:16px; padding-bottom:4px; }}
  .cat-btn {{ background:var(--surface); border:1px solid var(--border); color:var(--muted); border-radius:999px; padding:7px 14px; font-size:12px; font-weight:700; cursor:pointer; white-space:nowrap; }}
  .cat-btn.active {{ background:var(--accent); color:#04101d; border-color:var(--accent); }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(235px,1fr)); gap:14px; }}
  .card {{ background:var(--surface); border:1px solid var(--border); border-radius:16px; overflow:hidden; display:flex; flex-direction:column; justify-content:space-between; }}
  .card img {{ width:100%; height:150px; object-fit:cover; display:block; background:#0f172a; }}
  .card-body {{ padding:14px; display:flex; flex-direction:column; gap:7px; flex:1; }}
  .card h3 {{ font-size:15px; font-weight:800; }}
  .card p {{ font-size:12px; color:var(--muted); }}
  .price-row {{ display:flex; align-items:baseline; gap:8px; margin-top:auto; padding-top:6px; }}
  .price {{ font-size:18px; font-weight:900; color:var(--accent); }}
  .cmp {{ font-size:12px; color:var(--muted); text-decoration:line-through; }}
  .add-btn {{ width:100%; background:var(--accent); color:#04101d; border:none; border-radius:10px; padding:10px; font-weight:800; cursor:pointer; margin-top:8px; }}
  .drawer {{ position:fixed; right:0; top:0; bottom:0; width:min(380px,100vw); background:#0f172a; border-left:1px solid var(--border); z-index:60; padding:18px; display:none; flex-direction:column; gap:12px; }}
  .drawer.open {{ display:flex; }}
  .cart-item {{ background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:10px; display:flex; justify-content:space-between; align-items:center; gap:8px; font-size:13px; }}
  .qty-btn {{ background:rgba(255,255,255,0.1); color:#fff; border:none; border-radius:6px; width:24px; height:24px; cursor:pointer; font-weight:800; }}
  .promo-row {{ display:flex; gap:6px; }}
  .promo-row input {{ flex:1; background:#080b12; border:1px solid var(--border); color:#fff; border-radius:8px; padding:8px 10px; font-size:12px; }}
  .promo-row button {{ background:rgba(255,255,255,0.12); color:#fff; border:none; border-radius:8px; padding:8px 12px; font-weight:700; cursor:pointer; font-size:12px; }}
</style>
</head>
<body>
<div class="anno">{_html.escape(labels['promo'])}</div>
<header>
  <div class="brand">{safe_brand}</div>
  <button class="cart-btn" onclick="toggleCart(true)">🛒 {_html.escape(labels['cart'])} (<span id="cart-cnt">0</span>)</button>
</header>
<div class="wrap">
  <section class="hero">
    <h1>{safe_headline}</h1>
    <p>{safe_sub}</p>
    <button class="hero-cta" onclick="document.getElementById('catalog').scrollIntoView({{behavior:'smooth'}})">{safe_cta}</button>
  </section>
  <div class="usps">{usp_html}</div>
  <div class="cats" id="cats"></div>
  <div class="grid" id="catalog"></div>
</div>
<aside class="drawer" id="drawer">
  <div style="display:flex;justify-content:space-between;align-items:center;">
    <strong style="font-size:16px;">🛒 {_html.escape(labels['cart'])}</strong>
    <button class="qty-btn" onclick="toggleCart(false)">✕</button>
  </div>
  <div id="cart-items" style="flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:8px;"></div>
  <div class="promo-row">
    <input id="promo-inp" placeholder="{_html.escape(labels['coupon_ph'])}" />
    <button onclick="applyPromo()">{_html.escape(labels['apply'])}</button>
  </div>
  <div style="display:flex;justify-content:space-between;font-size:16px;font-weight:900;">
    <span>{_html.escape(labels['total'])}:</span>
    <span id="cart-total">{curr_sym}0</span>
  </div>
  <button class="add-btn" onclick="checkoutNow()">{_html.escape(labels['checkout'])}</button>
  <div id="checkout-status" style="font-size:12px;color:#34d399;"></div>
</aside>
<script>
const PRODUCTS = {json.dumps(products_js, ensure_ascii=False)};
const CURR = {json.dumps(curr_sym)};
const PROMO_CODE = {json.dumps(spec['promo_code'])};
let activeCat = "ALL";
let discount = 0;
let cart = {{}};

function fmtPrice(v) {{
  return CURR === "₽" ? Math.round(v).toLocaleString() + " ₽" : "$" + Number(v).toFixed(2);
}}
function renderCats() {{
  const cats = ["ALL", ...new Set(PRODUCTS.map(p => p.category))];
  document.getElementById("cats").innerHTML = cats.map(c =>
    `<button class="cat-btn ${{c===activeCat?'active':''}}" onclick="setCat('${{c}}')">${{c==="ALL" ? {json.dumps(labels['all'])} : c}}</button>`
  ).join("");
}}
function setCat(c) {{ activeCat = c; renderCats(); renderGrid(); }}
function renderGrid() {{
  const list = activeCat === "ALL" ? PRODUCTS : PRODUCTS.filter(p => p.category === activeCat);
  document.getElementById("catalog").innerHTML = list.map(p => `
    <div class="card">
      <img src="${{p.img}}" alt="${{p.title}}" />
      <div class="card-body">
        <div style="font-size:11px;color:var(--accent);font-weight:700;">${{p.category}} · ${{p.badge}}</div>
        <h3>${{p.title}}</h3>
        <p>${{p.tagline}}</p>
        <div class="price-row">
          <span class="price">${{fmtPrice(p.price)}}</span>
          <span class="cmp">${{fmtPrice(p.compare_at)}}</span>
        </div>
        <button class="add-btn" onclick="addCart('${{p.sku}}')">+ {_html.escape(labels['add'])}</button>
      </div>
    </div>`).join("");
}}
function addCart(sku) {{ cart[sku] = (cart[sku] || 0) + 1; updateCartUI(); toggleCart(true); }}
function chQty(sku, d) {{
  cart[sku] = (cart[sku] || 0) + d;
  if (cart[sku] <= 0) delete cart[sku];
  updateCartUI();
}}
function applyPromo() {{
  const v = (document.getElementById("promo-inp").value || "").trim().toUpperCase();
  discount = (v === PROMO_CODE) ? 0.15 : 0;
  updateCartUI();
}}
function updateCartUI() {{
  const entries = Object.entries(cart);
  const cnt = entries.reduce((s, [,q]) => s + q, 0);
  document.getElementById("cart-cnt").textContent = cnt;
  let total = 0;
  if (!entries.length) {{
    document.getElementById("cart-items").innerHTML = `<div style="color:var(--muted);font-size:13px;">{_html.escape(labels['empty'])}</div>`;
  }} else {{
    document.getElementById("cart-items").innerHTML = entries.map(([sku, q]) => {{
      const p = PRODUCTS.find(x => x.sku === sku);
      total += p.price * q;
      return `<div class="cart-item"><div><b>${{p.title}}</b><div style="color:var(--muted);font-size:11px;">${{fmtPrice(p.price)}} × ${{q}}</div></div><div style="display:flex;gap:4px;"><button class="qty-btn" onclick="chQty('${{sku}}',-1)">-</button><button class="qty-btn" onclick="chQty('${{sku}}',1)">+</button></div></div>`;
    }}).join("");
  }}
  const finalTotal = total * (1 - discount);
  document.getElementById("cart-total").textContent = fmtPrice(finalTotal) + (discount ? " (-15%)" : "");
}}
function toggleCart(open) {{ document.getElementById("drawer").classList.toggle("open", open); }}
function checkoutNow() {{
  const items = Object.entries(cart).map(([sku, q]) => ({{ sku, qty: q }}));
  if (!items.length) return;
  const payload = {{ store: {json.dumps(spec['brand_name'])}, promo: discount ? PROMO_CODE : "", items }};
  if (window.Telegram && window.Telegram.WebApp) {{
    try {{ window.Telegram.WebApp.sendData(JSON.stringify(payload)); }} catch(e) {{}}
  }}
  document.getElementById("checkout-status").textContent = {json.dumps(labels['order_sent'])};
}}
renderCats(); renderGrid(); updateCartUI();
</script>
</body>
</html>"""


def generate_traffic_and_sales_kit(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Produce high-converting Open V3 ad scripts, VSL hooks, and B2B sales pitch."""
    brand = spec["brand_name"]
    niche = spec["niche"]
    lang = spec.get("language", "ru")
    promo = spec["promo_code"]

    if lang == "ru":
        vsl_script = (
            f"[0–5 сек ХУК]: Не платите агентствам 150 000 ₽ за запуск магазина в нише «{niche}».\n"
            f"[5–20 сек БОЛЬ + ДЕМО]: Смотрите — наш ИИ Open V3 за 15 минут собрал готовый бренд «{brand}», "
            f"6 маржинальных товаров с описаниями, корзину для Shopify и Telegram Mini App без единой строчки кода.\n"
            f"[20–40 сек ОФФЕР]: Заберите готовую витрину бесплатно прямо сейчас: введите контакты, получите доступ к каталогу "
            f"и промокод {promo}. Никакой привязки карты.\n"
            f"[40–45 сек CTA]: Жмите по ссылке «Собрать магазин бесплатно» и запуститесь уже сегодня."
        )
        telegram_post = (
            f"🛍️ **Запуск AI-магазина в нише «{niche}» за 15 минут ({brand})**\n\n"
            f"Мы собрали готовый e-commerce конвейер Open V3 + Telegram Mini App:\n"
            f"• 6 протестированных SKU со средней маржой {spec['unit_economics']['margin_pct']}%\n"
            f"• Готовый экспорт `shopify_products.csv` + OS 2.0 `index.json` + HTML витрина\n"
            f"• Подключение оплаты (Shopify / ЮKassa / Prodamus / Telegram Stars)\n\n"
            f"🚀 Собрать такой же бесплатно или заказать запуск под ключ: {OPEN_V3_WEB_URL}"
        )
        b2b_pitch = (
            f"Привет! Вижу, вы развиваете направление «{niche}». Я собрал для вас интерактивный AI-прототип витрины "
            f"«{brand}» (работает и как сайт/Shopify, и как Telegram Mini App без комиссий маркетплейсов). "
            f"Могу скинуть готовый архив бесплатно — посмотрите, как легко поднять прямой чек и повторные заказы?"
        )
        ad_headlines = [
            f"Бесплатный AI-Магазин «{brand}» за 15 мин | Без карты",
            f"Shopify + Telegram витрина для «{niche}» за 1 клик",
            f"Запуск онлайн-магазина с каталогом и ИИ-продажником",
        ]
    else:
        vsl_script = (
            f"[0–5s HOOK]: Stop paying agencies $2,500+ to build your {niche} store.\n"
            f"[5–20s DEMO]: Watch our Open V3 AI engine generate '{brand}', 6 high-margin SKUs, "
            f"OS 2.0 Shopify theme JSON, and a Telegram storefront in under 15 minutes.\n"
            f"[20–40s OFFER]: Zero credit card needed. Claim your pre-built store bundle + {promo} promo engine today.\n"
            f"[40–45s CTA]: Click 'Build Your FREE Store' below before daily build slots fill up."
        )
        telegram_post = (
            f"🛍️ **Live Open V3 Store Blueprint: {brand} ({niche})**\n\n"
            f"• 6 Ready-to-Import SKUs (avg margin {spec['unit_economics']['margin_pct']}%)\n"
            f"• Shopify CSV + Online Store 2.0 JSON + Standalone HTML Storefront\n"
            f"• Instant checkout + affiliate & DFY monetization built in\n\n"
            f"Launch free in 15 min: {OPEN_V3_WEB_URL}"
        )
        b2b_pitch = (
            f"Hey! Built a live interactive AI storefront prototype ('{brand}') for {niche} with 6 high-margin SKUs, "
            f"cart recovery, and Telegram Mini App checkout. Happy to send the Shopify CSV + HTML bundle over for free — want the link?"
        )
        ad_headlines = [
            f"Build Your FREE AI Shopify Store ({brand}) | No Card Needed",
            f"Custom {niche} Store + 6 Winning Products in 15 Min",
            f"Done-For-You AI Store Builder · Free Trial Access",
        ]

    return {
        "vsl_script_45s": vsl_script,
        "telegram_post": telegram_post,
        "b2b_outreach_dm": b2b_pitch,
        "ad_headlines": ad_headlines,
    }


def generate_store_bundle(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build complete Open V3 AI store spec + all downloadable artifacts + traffic kit."""
    cfg = _load_runtime_config()
    first_name = str(payload.get("first_name") or "Partner").strip()[:50]
    last_name = str(payload.get("last_name") or "").strip()[:50]
    email = str(payload.get("email") or "partner@unityspirit.io").strip()[:100]
    phone = str(payload.get("phone") or "").strip()[:40]
    niche = str(payload.get("niche") or "Smart Tech & Lifestyle").strip()[:120]
    brand_hint = str(payload.get("brand_name") or "").strip()[:80]
    language = "en" if str(payload.get("language") or "ru").lower().startswith("en") else "ru"
    currency = "RUB" if str(payload.get("currency") or ("RUB" if language == "ru" else "USD")).upper() == "RUB" else "USD"
    platform = str(payload.get("platform") or "shopify_tma").strip()[:40]

    preset = _pick_preset(niche)
    ai_json, engine_used = _call_store_llm(niche, brand_hint, language, cfg)

    brand_name = brand_hint or (
        (ai_json.get("brand_name") if ai_json else "")
        or (preset["brand_ru"] if language == "ru" else preset["brand_en"])
    )
    brand_name = str(brand_name).strip()[:60]
    slug_base = _slugify(f"{brand_name}-{niche}")
    short_hex = secrets.token_hex(2)
    slug = f"{slug_base}-{short_hex}"
    store_id = f"GSG-{short_hex.upper()}-{datetime.datetime.now().strftime('%m%d')}"

    if ai_json:
        tagline = str(ai_json.get("tagline") or (preset["tagline_ru"] if language == "ru" else preset["tagline_en"]))[:180]
        hero_headline = str(ai_json.get("hero_headline") or f"{brand_name} — {niche}")[:120]
        hero_subheadline = str(ai_json.get("hero_subheadline") or tagline)[:220]
        usp_list = [str(u)[:60] for u in (ai_json.get("usp_list") or [])[:4]]
        raw_prods = ai_json.get("products") or []
    else:
        tagline = preset["tagline_ru"] if language == "ru" else preset["tagline_en"]
        hero_headline = f"{brand_name}: {'следующее поколение' if language=='ru' else 'Next-Gen'} {niche}"
        hero_subheadline = tagline
        usp_list = (
            ["Доставка за 2–4 дня", "Оплата при получении / Картой / Stars", "Гарантия возврата 30 дней", "Поддержка AI-консультанта 24/7"]
            if language == "ru"
            else ["Fast 48h Dispatch", "Encrypted Checkout & Express Pay", "30-Day Money-Back Guarantee", "24/7 AI Concierge Support"]
        )
        raw_prods = []

    if len(usp_list) < 4:
        usp_list = (usp_list + ["Verified Quality", "24/7 AI Support", "Express Checkout", "30-Day Guarantee"])[:4]

    products: List[Dict[str, Any]] = []
    source_list = raw_prods[:6] if raw_prods else preset["products"]
    for idx, item in enumerate(source_list, start=1):
        if isinstance(item, dict):
            title = str(item.get("title") or f"{brand_name} Item #{idx}")[:80]
            cat = str(item.get("category") or "Featured")[:40]
            p_tag = str(item.get("tagline") or "High-converting bestseller")[:120]
            p_desc = str(item.get("description") or f"{title} engineered for maximum daily performance in {niche}.")[:400]
            price_usd = float(item.get("price_usd") or 49.0)
            cmp_usd = float(item.get("compare_at_usd") or round(price_usd * 1.55, 2))
            cogs_usd = float(item.get("cogs_usd") or round(price_usd * 0.24, 2))
            badge = str(item.get("badge") or "Trending")[:24]
        else:
            title_en, title_ru, cat, price_usd, cmp_usd, cogs_usd, badge = item
            title = title_ru if language == "ru" else title_en
            p_tag = f"{badge} · {cat} ({niche})"
            p_desc = (
                f"{title} — отобранный высокомаржинальный хит ниши «{niche}» с быстрой окупаемостью рекламы и высоким LTV."
                if language == "ru"
                else f"{title} — validated high-margin winner in {niche} optimized for impulse AOV and repeat purchases."
            )
        margin_pct = round(max(10.0, ((price_usd - cogs_usd) / max(1.0, price_usd)) * 100.0), 1)
        products.append({
            "sku": f"GSG-{short_hex.upper()}-{idx:02d}",
            "handle": _slugify(title, max_len=48),
            "title": title,
            "category": cat,
            "tagline": p_tag,
            "description": p_desc,
            "price_usd": round(price_usd, 2),
            "compare_at_usd": round(max(cmp_usd, price_usd * 1.25), 2),
            "cogs_usd": round(cogs_usd, 2),
            "price_rub": int(round(price_usd * 95 / 10.0) * 10),
            "compare_at_rub": int(round(max(cmp_usd, price_usd * 1.25) * 95 / 10.0) * 10),
            "margin_pct": margin_pct,
            "badge": badge,
        })

    avg_order_usd = round(sum(p["price_usd"] for p in products) / max(1, len(products)) * 1.28, 2)
    avg_margin = round(sum(p["margin_pct"] for p in products) / max(1, len(products)), 1)
    shopify_partner_url = (cfg.get("shopify_partner_url") or DEFAULT_SHOPIFY_TRIAL_URL).strip()

    spec: Dict[str, Any] = {
        "store_id": store_id,
        "slug": slug,
        "created_at": _utc_now_iso(),
        "engine_used": engine_used,
        "language": language,
        "currency": currency,
        "platform": platform,
        "niche": niche,
        "brand_name": brand_name,
        "tagline": tagline,
        "hero_headline": hero_headline,
        "hero_subheadline": hero_subheadline,
        "cta_text": "Смотреть каталог (-15%)" if language == "ru" else "Shop Bestsellers (-15%)",
        "promo_code": "WELCOME15",
        "palette": preset["palette"],
        "usp_list": usp_list,
        "products": products,
        "pages": {
            "home": f"Hero + 6 SKU Catalog + {brand_name} Trust Banner",
            "shipping_returns": (
                "Бесплатная доставка от 4 900 ₽ · 30 дней гарантии возврата · Круглосуточный AI-сервис."
                if language == "ru"
                else "Free express shipping on bundles · 30-day risk-free guarantee · 24/7 AI Concierge."
            ),
            "privacy_policy": f"{brand_name} Encrypted Storefront Data Policy ({email})",
        },
        "lead": {
            "first_name": first_name,
            "last_name": last_name,
            "full_name": f"{first_name} {last_name}".strip(),
            "email": email,
            "phone": phone,
            "utm_source": str(payload.get("utm_source") or "open_v3_direct"),
        },
        "admin_credentials": {
            "admin_email": email,
            "temp_password": _generate_store_password(12),
            "store_portal_url": f"{OPEN_V3_WEB_URL}?store={slug}",
            "shopify_partner_trial_url": shopify_partner_url,
            "telegram_bot_deeplink": f"https://t.me/unitybusinessbot?start=store_{slug}",
        },
        "unit_economics": {
            "avg_order_usd": avg_order_usd,
            "margin_pct": avg_margin,
            "projected_monthly_gmv_usd": round(avg_order_usd * 65, 2),
            "office_monetization": {
                "shopify_cpa_usd": 150,
                "pro_saas_usd": 29,
                "pro_saas_rub": 1490,
                "dfy_turnkey_usd": 750,
                "dfy_turnkey_rub": 75000,
                "expected_blended_ltv_usd": 389,
            },
        },
    }

    artifacts = {
        "shopify_products.csv": build_shopify_products_csv(spec),
        "shopify_index_os2.json": build_shopify_os2_index_json(spec),
        "standalone_storefront.html": build_standalone_storefront_html(spec),
        "telegram_mini_app_config.json": build_telegram_mini_app_config(spec),
    }
    traffic_kit = generate_traffic_and_sales_kit(spec)

    return {
        "spec": spec,
        "artifacts": artifacts,
        "traffic_kit": traffic_kit,
    }


def _safe_store_dir(slug: str) -> Path:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", slug or "ai-store").strip("-") or "ai-store"
    dest = (PRODUCTS_OUTPUT_ROOT / safe_name).resolve()
    root_res = PRODUCTS_OUTPUT_ROOT.resolve()
    if root_res != dest and root_res not in dest.parents:
        raise ValueError(f"Unsafe output path for slug: {slug}")
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _rewrite_pipeline_summary() -> Dict[str, Any]:
    LEADS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    if LEADS_JSONL_PATH.exists():
        for ln in LEADS_JSONL_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = ln.strip()
            if not s:
                continue
            try:
                rows.append(json.loads(s))
            except Exception:
                continue

    total_leads = len(rows)
    shopify_cpa_pool = sum(float(r.get("shopify_cpa_usd", 150)) for r in rows)
    pro_mrr_pool = sum(float(r.get("pro_saas_usd", 29)) for r in rows)
    dfy_pool = sum(float(r.get("dfy_turnkey_usd", 750)) * 0.15 for r in rows)  # conservative 15% DFY close rate

    md_lines = [
        "# 💰 Open V3 AI Store Builder — Реестр Лидов и Выручки Офиса\n",
        f"- **Обновлено:** {_utc_now_iso()}",
        f"- **Всего сгенерировано магазинов / лидов:** **{total_leads}**",
        f"- **Пул Shopify Partner CPA ($150/триал):** **${shopify_cpa_pool:,.0f}**",
        f"- **Пул Pro SaaS MRR ($29/мес):** **${pro_mrr_pool:,.0f}/мес**",
        f"- **Консервативный прогноз DFY апсейлов (15% × $750):** **${dfy_pool:,.0f}**\n",
        "## Последние сборки и лиды\n",
        "| Дата | Store ID | Бренд / Ниша | Контакт | Движок ИИ | Статус |",
        "|------|----------|--------------|---------|-----------|--------|",
    ]
    for r in reversed(rows[-30:]):
        dt = str(r.get("created_at") or "")[:16].replace("T", " ")
        md_lines.append(
            f"| {dt} | `{r.get('store_id')}` | {r.get('brand_name')} ({r.get('niche')}) | "
            f"{r.get('full_name')} ({r.get('email')}) | {r.get('engine_used')} | {r.get('status', 'built')} |"
        )
    PIPELINE_MD_PATH.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return {
        "total_leads": total_leads,
        "shopify_cpa_pool_usd": round(shopify_cpa_pool, 2),
        "pro_mrr_pool_usd": round(pro_mrr_pool, 2),
        "projected_dfy_usd": round(dfy_pool, 2),
        "recent_leads": list(reversed(rows[-20:])),
    }


def _sync_lead_to_github_crm(lead_record: Dict[str, Any]) -> bool:
    """Persist new store lead into cloud data/crm_data.json across configured repos."""
    cfg = _load_runtime_config()
    targets: List[Tuple[str, str]] = []
    if cfg.get("gtp_token") and cfg.get("gtp_repo"):
        targets.append((cfg["gtp_repo"], cfg["gtp_token"]))
    if cfg.get("u_token") and cfg.get("u_repo") and cfg.get("u_repo") != cfg.get("gtp_repo"):
        targets.append((cfg["u_repo"], cfg["u_token"]))
    if not targets:
        return False

    any_ok = False
    for repo, token in targets:
        url = f"https://api.github.com/repos/{repo}/contents/crm_data.json?ref=data"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GSG-OpenV3-Engine",
        }
        sha = None
        crm_obj: Dict[str, Any] = {"version": "34.3", "knowledge_base": []}
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
                meta = json.loads(r.read().decode("utf-8"))
                sha = meta.get("sha")
                raw = base64.b64decode(meta["content"].replace("\n", "")).decode("utf-8")
                crm_obj = json.loads(raw)
        except urllib.error.HTTPError as he:
            if he.code != 404:
                continue
        except Exception:
            continue

        leads = crm_obj.setdefault("store_leads", [])
        if not isinstance(leads, list):
            leads = []
            crm_obj["store_leads"] = leads
        if not any(x.get("store_id") == lead_record.get("store_id") for x in leads if isinstance(x, dict)):
            leads.insert(0, lead_record)
            crm_obj["store_leads"] = leads[:150]

        crm_obj["revenue_pipeline"] = {
            "updated_at": _utc_now_iso(),
            "store_leads_count": len(crm_obj["store_leads"]),
            "estimated_cpa_pool_usd": round(sum(float(x.get("shopify_cpa_usd", 150)) for x in crm_obj["store_leads"]), 2),
            "estimated_mrr_usd": round(sum(float(x.get("pro_saas_usd", 29)) for x in crm_obj["store_leads"]), 2),
        }
        crm_obj["updated_at"] = _utc_now_iso()

        put_body: Dict[str, Any] = {
            "message": f"Open V3 Store Lead: {lead_record.get('store_id')} [v34.3]",
            "content": base64.b64encode(json.dumps(crm_obj, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii"),
            "branch": "data",
        }
        if sha:
            put_body["sha"] = sha
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{repo}/contents/crm_data.json",
                data=json.dumps(put_body).encode("utf-8"),
                headers={**headers, "Content-Type": "application/json"},
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=20) as pr:
                if pr.status in (200, 201):
                    any_ok = True
        except Exception:
            continue
    return any_ok


def _notify_owner_telegram(bundle: Dict[str, Any], saved_dir: Path) -> bool:
    """Notify @unityspiritpartners in Telegram when a new Open V3 store & lead is created."""
    cfg = _load_runtime_config()
    bot_token = (cfg.get("tg_bot_token") or os.environ.get("TG_BOT_TOKEN") or "").strip()
    owner_chat = str(cfg.get("owner_tg_chat_id") or os.environ.get("OWNER_TG_CHAT_ID") or "").strip()
    if not bot_token or not owner_chat:
        return False

    spec = bundle["spec"]
    lead = spec["lead"]
    econ = spec["unit_economics"]["office_monetization"]
    text = (
        f"🛍️ <b>OPEN V3: СОЗДАН НОВЫЙ AI-МАГАЗИН + ЛИД!</b>\n\n"
        f"• ID: <code>{_html.escape(spec['store_id'])}</code> | ИИ: <code>{_html.escape(str(spec['engine_used']))}</code>\n"
        f"• Бренд: <b>{_html.escape(spec['brand_name'])}</b> ({_html.escape(spec['niche'])})\n"
        f"• Клиент: <b>{_html.escape(lead['full_name'])}</b>\n"
        f"• Email: <code>{_html.escape(lead['email'])}</code>\n"
        f"• Тел/TG: <code>{_html.escape(lead['phone'] or 'не указан')}</code>\n"
        f"• Товаров: <b>{len(spec['products'])}</b> | Средний чек: <b>${spec['unit_economics']['avg_order_usd']}</b>\n"
        f"• Монетизация лида: CPA <b>${econ['shopify_cpa_usd']}</b> + Pro <b>${econ['pro_saas_usd']}/мес</b> + DFY <b>${econ['dfy_turnkey_usd']}</b>\n"
        f"• Файлы на ПК: <code>{_html.escape(str(saved_dir))}</code>"
    )
    payload = {
        "chat_id": owner_chat,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "🛍️ Открыть Open V3 Билдер", "url": OPEN_V3_WEB_URL}],
                [{"text": "💰 Статус выручки (/revenue)", "callback_data": "cmd:revenue"}],
            ]
        },
    }
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            return bool(res.get("ok"))
    except Exception:
        return False


def build_and_persist_store(
    payload: Dict[str, Any],
    sync_github: bool = True,
    notify_telegram: bool = True,
) -> Dict[str, Any]:
    """Generate store bundle, write local deliverables, record CRM lead, and alert Owner."""
    bundle = generate_store_bundle(payload)
    spec = bundle["spec"]
    slug = spec["slug"]
    out_dir = _safe_store_dir(slug)

    (out_dir / "store_spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    for fname, content in bundle["artifacts"].items():
        (out_dir / fname).write_text(content, encoding="utf-8")

    kit = bundle["traffic_kit"]
    kit_md = (
        f"# {spec['brand_name']} — Launch & Monetization Kit ({spec['store_id']})\n\n"
        f"- **Ниша:** {spec['niche']}\n"
        f"- **Клиент:** {spec['lead']['full_name']} ({spec['lead']['email']} / {spec['lead']['phone']})\n"
        f"- **Storefront URL Preview:** {spec['admin_credentials']['store_portal_url']}\n"
        f"- **Admin Email:** `{spec['admin_credentials']['admin_email']}`\n"
        f"- **Generated Temp Password:** `{spec['admin_credentials']['temp_password']}`\n\n"
        f"## VSL 45-Sec Script\n```\n{kit['vsl_script_45s']}\n```\n\n"
        f"## Telegram Post\n{kit['telegram_post']}\n\n"
        f"## B2B DFY Outreach DM\n{kit['b2b_outreach_dm']}\n\n"
        f"## Ad Headlines\n" + "\n".join(f"- {h}" for h in kit["ad_headlines"]) + "\n"
    )
    (out_dir / "LAUNCH_AND_MONETIZATION_KIT.md").write_text(kit_md, encoding="utf-8")

    econ = spec["unit_economics"]["office_monetization"]
    lead_record = {
        "store_id": spec["store_id"],
        "slug": slug,
        "created_at": spec["created_at"],
        "brand_name": spec["brand_name"],
        "niche": spec["niche"],
        "language": spec["language"],
        "currency": spec["currency"],
        "engine_used": spec["engine_used"],
        "full_name": spec["lead"]["full_name"],
        "email": spec["lead"]["email"],
        "phone": spec["lead"]["phone"],
        "utm_source": spec["lead"]["utm_source"],
        "products_count": len(spec["products"]),
        "avg_order_usd": spec["unit_economics"]["avg_order_usd"],
        "shopify_cpa_usd": econ["shopify_cpa_usd"],
        "pro_saas_usd": econ["pro_saas_usd"],
        "dfy_turnkey_usd": econ["dfy_turnkey_usd"],
        "local_dir": str(out_dir),
        "status": "built_ready_to_monetize",
    }

    LEADS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(LEADS_JSONL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(lead_record, ensure_ascii=False) + "\n")

    pipeline_summary = _rewrite_pipeline_summary()
    gh_synced = _sync_lead_to_github_crm(lead_record) if sync_github else False
    tg_sent = _notify_owner_telegram(bundle, out_dir) if notify_telegram else False

    return {
        "ok": True,
        "store_id": spec["store_id"],
        "slug": slug,
        "saved_dir": str(out_dir),
        "gh_synced": gh_synced,
        "tg_notified": tg_sent,
        "pipeline_summary": pipeline_summary,
        "bundle": bundle,
    }


def get_revenue_summary() -> Dict[str, Any]:
    """Read local ledger & return live monetization metrics."""
    return _rewrite_pipeline_summary()


if __name__ == "__main__":
    import sys
    arg_niche = " ".join(sys.argv[1:]).strip() or "Пептидный уход для лица (AI Beauty)"
    res = build_and_persist_store(
        {"first_name": "Vlaslav", "last_name": "Test", "email": "demo@globalspirit.group", "niche": arg_niche, "language": "ru"},
        sync_github=False,
        notify_telegram=False,
    )
    print(json.dumps({"ok": res["ok"], "store_id": res["store_id"], "saved_dir": res["saved_dir"], "engine": res["bundle"]["spec"]["engine_used"]}, ensure_ascii=False))
