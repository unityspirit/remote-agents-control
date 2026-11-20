#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Office Money Trend Radar Bridge (v34.7)
Connects Unity_Spirit_Workspace/unity-ruflo-agents/agents/money_trend_radar.js
and DAILY_MONEY_TRENDS.md directly into live @unitybusinessbot & Director Tim NLU.
"""
from __future__ import annotations

import datetime
import html as _html
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

PANEL_DIR = Path(__file__).resolve().parent
DATA_DIR = PANEL_DIR / "data"
STATE_FILE = DATA_DIR / "money_trends_state.json"
RUFLO_DIR = Path(r"c:\Users\Vlaslav\OneDrive\Desktop\delaem prezervativ\Unity_Spirit_Workspace\unity-ruflo-agents")
RADAR_SCRIPT = RUFLO_DIR / "agents" / "money_trend_radar.js"
BRIEFING_MD = RUFLO_DIR / "DAILY_MONEY_TRENDS.md"
SIGNALS_JSON = RUFLO_DIR / "data" / "money_trends_latest.json"

_RUN_LOCK = threading.Lock()


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.is_file():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(obj: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE_FILE)


def get_cached_briefing_markdown(max_chars: int = 3400) -> str:
    """Return latest persisted DAILY_MONEY_TRENDS.md text if present."""
    if not BRIEFING_MD.is_file():
        return ""
    try:
        txt = BRIEFING_MD.read_text(encoding="utf-8", errors="replace").strip()
        return txt[:max_chars]
    except OSError:
        return ""


def get_compact_memory_snippet(max_chars: int = 700) -> str:
    """Return a concise summary of today's Top 3 Money Plays for Director Tim's prompt memory."""
    raw = get_cached_briefing_markdown(max_chars=4500)
    if not raw:
        return ""
    plays = re.findall(r"^\s*\d+\.\s+\*\*(.+?)\*\*", raw, flags=re.MULTILINE)
    date_m = re.search(r"\((\d{4}-\d{2}-\d{2})\)", raw)
    stamp = date_m.group(1) if date_m else datetime.date.today().isoformat()
    if plays:
        joined = "; ".join(p.strip() for p in plays[:4])
        return f"- [Money Trend Radar {stamp}]: Top Plays: {joined}"[:max_chars]
    first_lines = " ".join(l.strip() for l in raw.splitlines()[:6] if l.strip())
    return f"- [Money Trend Radar {stamp}]: {first_lines}"[:max_chars]


def format_briefing_for_telegram(max_len: int = 3500) -> str:
    """Format the latest DAILY_MONEY_TRENDS.md cleanly as Telegram HTML."""
    raw = get_cached_briefing_markdown(max_chars=8000)
    if not raw:
        return (
            "📈 <b>DAILY MONEY &amp; TREND RADAR</b>\n\n"
            "Свежий отчёт ещё не сформирован. Нажмите кнопку ниже или отправьте <code>/trends refresh</code>."
        )
    mtime_str = ""
    try:
        mt = datetime.datetime.fromtimestamp(BRIEFING_MD.stat().st_mtime)
        mtime_str = mt.strftime("%Y-%m-%d %H:%M")
    except OSError:
        pass

    lines = []
    for ln in raw.splitlines():
        s = ln.strip()
        if not s:
            lines.append("")
            continue
        if s.startswith("# "):
            lines.append(f"<b>{_html.escape(s[2:].strip())}</b>")
        elif s.startswith("## "):
            lines.append(f"\n<b>{_html.escape(s[3:].strip())}</b>")
        else:
            esc = _html.escape(s)
            esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
            esc = re.sub(r"`([^`]+)`", r"<code>\1</code>", esc)
            lines.append(esc)

    body = "\n".join(lines).strip()
    footer = f"\n\n⏱️ <i>Обновлено: {_html.escape(mtime_str or 'сегодня')} · Авто-дайджест: ежедневно в 09:30</i>\n<i>Команды:</i> <code>/trends</code> · <code>/trends refresh</code> · <code>/trends real estate</code>"
    budget = max(500, max_len - len(footer))
    if len(body) > budget:
        body = body[:budget].rsplit("\n", 1)[0] + "\n..."
    return body + footer


def run_fresh_trend_scan(niche: str = "", timeout: int = 120) -> dict[str, Any]:
    """Execute Node money_trend_radar.js safely (serialized via lock)."""
    if not RADAR_SCRIPT.is_file():
        return {"ok": False, "error": f"Script not found: {RADAR_SCRIPT}"}
    if not _RUN_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "scan_already_running"}
    try:
        cmd = ["node", str(RADAR_SCRIPT)]
        if niche and niche.lower() not in ("refresh", "now", "scan", "обновить"):
            cmd.append(niche)
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
        res = subprocess.run(
            cmd,
            cwd=str(RUFLO_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=no_window,
        )
        ok = res.returncode == 0
        st = _load_state()
        st.update({
            "last_run_epoch": time.time(),
            "last_run_date": datetime.date.today().isoformat(),
            "last_niche": niche,
            "ok": ok,
        })
        _save_state(st)
        return {
            "ok": ok,
            "returncode": res.returncode,
            "stdout_tail": (res.stdout or "")[-600:],
            "stderr_tail": (res.stderr or "")[-400:],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        _RUN_LOCK.release()


def should_run_daily_0930_briefing() -> bool:
    """True once per calendar day at or after 09:30 local time if not yet sent today."""
    now = datetime.datetime.now()
    if now.hour < 9 or (now.hour == 9 and now.minute < 30):
        return False
    today = now.date().isoformat()
    st = _load_state()
    if st.get("last_daily_sent_date") == today:
        return False
    return True


def mark_daily_briefing_sent(today_str: str | None = None) -> None:
    st = _load_state()
    st["last_daily_sent_date"] = today_str or datetime.date.today().isoformat()
    st["last_daily_sent_ts"] = datetime.datetime.now().isoformat(timespec="seconds")
    _save_state(st)
