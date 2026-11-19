#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Autonomous Startup-First Office SEO Worker (v34.5 · AgriciDaniel/claude-seo v2.6.2).

Runs FIRST at computer startup (via Windows Startup `00_Start_Unity_SEO_First.vbs`
and Task Scheduler / `tg_bot_poller.py` priority boot phase) before launching or
entering the main Office daemon loop.

Uses installed Claude SEO scripts in `C:\\Users\\Vlaslav\\.claude\\skills\\seo\\scripts\\`:
  - `page_checks.py` (47 deterministic on-page checks)
  - `parse_html.py` (HTML / headings / schema / OG / canonical / hidden text)
  - `agentic_check.py` (AI crawler robots.txt, llms.txt, ARD, WebMCP)
  - `sitemap_discovery.py` (robots.txt + sitemap discovery & URL hygiene)
"""
from __future__ import annotations

import argparse
import datetime
import html as _html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OFFICE_DIR = Path(__file__).resolve().parent
DATA_DIR = OFFICE_DIR / "data"
INBOX_SEO_DIR = Path(r"C:\Users\Vlaslav\Downloads\Входящие_Офис\Отчеты_SEO_Агента")
PACKS_DIR = INBOX_SEO_DIR / "packs"
CONFIG_FILE = OFFICE_DIR / ".tg_daemon_config.json"
MANIFEST_FILE = OFFICE_DIR / "seo_skills_manifest.json"
STATE_FILE = DATA_DIR / "seo_autopilot_state.json"
QUEUE_FILE = DATA_DIR / "seo_tasks_queue.json"
DRIFT_FILE = DATA_DIR / "seo_drift_history.json"
LOG_FILE = DATA_DIR / "seo_startup.log"

CLAUDE_SEO_HOME = Path(r"C:\Users\Vlaslav\.claude\skills\seo")
CLAUDE_SEO_SCRIPTS = CLAUDE_SEO_HOME / "scripts"
CLAUDE_SEO_RUNTIME = CLAUDE_SEO_SCRIPTS / "runtime.py"
CLAUDE_SEO_VENV_PY = CLAUDE_SEO_HOME / ".venv" / "Scripts" / "python.exe"
SYS_PY312 = Path(r"C:\Users\Vlaslav\AppData\Local\Programs\Python\Python312\python.exe")

DEFAULT_STARTUP_COOLDOWN_SEC = 900  # 15 min guard against duplicate rapid restarts

LOCAL_HTML_TARGETS = [
    ("index.html", "https://globalteampartners.github.io/remote-agents-control/"),
    ("index_en.html", "https://globalteampartners.github.io/remote-agents-control/index_en.html"),
    ("ai_store_builder/index.html", "https://globalteampartners.github.io/remote-agents-control/ai_store_builder/"),
    ("Traffic/index.html", "https://globalteampartners.github.io/remote-agents-control/Traffic/"),
    ("12_IT_Products/index.html", "https://globalteampartners.github.io/remote-agents-control/12_IT_Products/"),
    (
        "AI_Skills_And_Macro_Stabilization/index.html",
        "https://globalteampartners.github.io/remote-agents-control/AI_Skills_And_Macro_Stabilization/",
    ),
    ("globalfilms/index.html", "https://globalteampartners.github.io/remote-agents-control/globalfilms/"),
    ("agents_live/index.html", "https://globalteampartners.github.io/remote-agents-control/agents_live/"),
    ("Unity_Office_App_Installer.html", "https://globalteampartners.github.io/remote-agents-control/Unity_Office_App_Installer.html"),
]

LIVE_SITE_TARGETS = [
    {
        "name": "Virtual Office HQ (Primary RU)",
        "url": "https://globalteampartners.github.io/remote-agents-control/",
        "root": "https://globalteampartners.github.io/remote-agents-control",
        "role": "core_office",
    },
    {
        "name": "Virtual Office HQ (EN)",
        "url": "https://globalteampartners.github.io/remote-agents-control/index_en.html",
        "root": "",
        "role": "core_office",
    },
    {
        "name": "Open V3 AI Store Builder",
        "url": "https://globalteampartners.github.io/remote-agents-control/ai_store_builder/",
        "root": "",
        "role": "revenue_funnel",
    },
    {
        "name": "Virtual Office Mirror (UnitySpirit)",
        "url": "https://unityspirit.github.io/remote-agents-control/",
        "root": "",
        "role": "mirror",
    },
    {
        "name": "Alberta Business Hub",
        "url": "https://unityspirit.github.io/alberta-hub/",
        "root": "https://unityspirit.github.io/alberta-hub",
        "role": "project_hub",
    },
    {
        "name": "Global Plus Tech",
        "url": "https://globalplus.tech/",
        "root": "https://globalplus.tech",
        "role": "brand_domain",
    },
    {
        "name": "Unity Spirit Partners",
        "url": "https://unityspiritpartners.com/",
        "root": "https://unityspiritpartners.com",
        "role": "brand_domain",
    },
]

_SEO_MUTEX_HANDLE = None


def _utc_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local_stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str) -> None:
    line = f"[{_local_stamp()}] [SEO-FIRST] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as lf:
            lf.write(line + "\n")
    except OSError:
        pass


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def acquire_seo_mutex() -> bool:
    """Prevent concurrent startup SEO runs across Task Scheduler / VBS / Poller."""
    global _SEO_MUTEX_HANDLE
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            mutex = kernel32.CreateMutexW(None, False, "Local\\GSG_Unity_SEO_First_V34")
            err = kernel32.GetLastError()
            if not mutex:
                return True
            if err == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(mutex)
                return False
            _SEO_MUTEX_HANDLE = mutex
            return True
        except Exception:
            pass
    lock_file = DATA_DIR / ".seo_worker.lock"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if lock_file.is_file():
        try:
            age = time.time() - lock_file.stat().st_mtime
            if age < 600:
                return False
        except OSError:
            pass
    try:
        lock_file.write_text(f"{os.getpid()}\n{int(time.time())}\n", encoding="utf-8")
    except OSError:
        pass
    return True


def _resolve_python_exe() -> Path:
    if CLAUDE_SEO_VENV_PY.is_file():
        return CLAUDE_SEO_VENV_PY
    if SYS_PY312.is_file():
        return SYS_PY312
    return Path(sys.executable)


def run_claude_seo_script(script_name: str, args: list[str], timeout: int = 45) -> dict[str, Any]:
    """Execute one bundled Claude SEO script cleanly and parse its JSON stdout."""
    script_path = CLAUDE_SEO_SCRIPTS / script_name
    if not script_path.is_file():
        return {"error": f"Claude SEO script missing: {script_path}"}
    py_exe = _resolve_python_exe()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [str(py_exe), "-X", "utf8", str(script_path), *args]
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    try:
        res = subprocess.run(
            cmd,
            cwd=str(CLAUDE_SEO_SCRIPTS),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=no_window,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"{script_name} timed out after {timeout}s"}
    except OSError as exc:
        return {"error": f"{script_name} launch failed: {exc}"}

    raw_out = (res.stdout or "").strip()
    if not raw_out:
        err = (res.stderr or "").strip()
        return {"error": err or f"{script_name} returned empty output (rc={res.returncode})"}
    try:
        parsed = json.loads(raw_out)
        if isinstance(parsed, dict):
            return parsed
        return {"result": parsed}
    except ValueError:
        return {
            "error": f"{script_name} returned non-JSON (rc={res.returncode})",
            "raw": raw_out[:300],
            "stderr": (res.stderr or "")[:200],
        }


def compute_seo_score(page_check: dict[str, Any], parse_data: dict[str, Any] | None = None) -> int:
    """Compute a deterministic 0-100 on-page health score from page_checks + parse_html."""
    if page_check.get("error"):
        return 0
    summary = page_check.get("summary") or {}
    crit = int(summary.get("Critical", 0))
    high = int(summary.get("High", 0))
    med = int(summary.get("Medium", 0))
    low = int(summary.get("Low", 0))
    score = 100 - (crit * 22 + high * 10 + med * 4 + low * 1)
    if parse_data and not parse_data.get("error"):
        if not parse_data.get("schema"):
            score -= 4
        if not parse_data.get("canonical"):
            score -= 5
        if not parse_data.get("meta_description"):
            score -= 6
    return max(0, min(100, int(score)))


def _audit_single_local_html(item: tuple[str, str]) -> dict[str, Any]:
    rel_path, base_url = item
    full_path = OFFICE_DIR / rel_path
    if not full_path.is_file():
        return {"file": rel_path, "url": base_url, "error": "file missing"}
    pc = run_claude_seo_script(
        "page_checks.py",
        ["--file", str(full_path), "--base-url", base_url],
        timeout=25,
    )
    ph = run_claude_seo_script(
        "parse_html.py",
        [str(full_path), "--json"],
        timeout=25,
    )
    summary = pc.get("summary") or {}
    findings = pc.get("findings") or []
    return {
        "file": rel_path,
        "url": base_url,
        "score": compute_seo_score(pc, ph),
        "title": ph.get("title") or "",
        "has_description": bool(ph.get("meta_description")),
        "has_canonical": bool(ph.get("canonical")),
        "has_og": bool(ph.get("open_graph")),
        "schema_types": [s.get("@type") for s in (ph.get("schema") or []) if isinstance(s, dict)],
        "summary": summary,
        "top_findings": [f.get("id") for f in findings[:6] if isinstance(f, dict)],
        "error": pc.get("error") or ph.get("error") or "",
    }


def audit_local_html_files() -> list[dict[str, Any]]:
    """Run Claude SEO page_checks.py & parse_html.py in parallel against every Office HTML page."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(_audit_single_local_html, LOCAL_HTML_TARGETS))


def _audit_single_live_url(t: dict[str, str]) -> dict[str, Any]:
    url = t["url"]
    root = t.get("root") or ""
    pc = run_claude_seo_script("page_checks.py", ["--url", url], timeout=50)
    ph = run_claude_seo_script("parse_html.py", ["--url", url, "--json"], timeout=50)
    score = compute_seo_score(pc, ph)
    summary = pc.get("summary") or {}
    findings = [f for f in (pc.get("findings") or []) if isinstance(f, dict)]

    agentic = {}
    sitemap_info = {}
    if root:
        ag = run_claude_seo_script("agentic_check.py", [root, "--json"], timeout=30)
        sm = run_claude_seo_script("sitemap_discovery.py", [root, "--json"], timeout=25)
        ag_data = ag.get("data") if isinstance(ag.get("data"), dict) else {}
        rob = ag_data.get("robots") or {}
        llms_obj = ag_data.get("llms") or {}
        pg = ag_data.get("page") or {}
        robots_found = (rob.get("status") == 200)
        llms_found = (llms_obj.get("status") == 200)
        found_sitemaps = sm.get("found") if isinstance(sm.get("found"), list) else []

        def _probe_subpath(rel_file: str) -> bool:
            try:
                req = urllib.request.Request(f"{root.rstrip('/')}/{rel_file}", headers={"User-Agent": "GSG-SEO-Probe"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    return r.status == 200
            except Exception:
                return False

        if not robots_found and urlparse(root).path.strip("/"):
            robots_found = _probe_subpath("robots.txt")
        if not llms_found and urlparse(root).path.strip("/"):
            llms_found = _probe_subpath("llms.txt")
        if not found_sitemaps and urlparse(root).path.strip("/") and _probe_subpath("sitemap.xml"):
            found_sitemaps = [{"url": f"{root.rstrip('/')}/sitemap.xml", "status_code": 200, "kind": "urlset"}]

        agentic = {
            "robots_found": robots_found,
            "llms_found": llms_found,
            "llms_verdict": llms_obj.get("lighthouse") or "",
            "server_words": pg.get("words_without_js"),
            "error": ag.get("error") or "",
        }
        sitemap_info = {
            "count": len(found_sitemaps),
            "first_status": found_sitemaps[0].get("status_code") if found_sitemaps else None,
            "first_url": found_sitemaps[0].get("url") if found_sitemaps else "",
            "error": sm.get("error") or "",
        }

    return {
        "name": t.get("name") or url,
        "url": url,
        "role": t.get("role") or "custom",
        "status_code": pc.get("status_code"),
        "score": score,
        "title": ph.get("title") or "",
        "has_description": bool(ph.get("meta_description")),
        "has_canonical": bool(ph.get("canonical")),
        "has_og": bool(ph.get("open_graph")),
        "schema_count": len(ph.get("schema") or []),
        "summary": summary,
        "top_findings": [f.get("id") for f in findings[:6]],
        "agentic": agentic,
        "sitemap": sitemap_info,
        "error": pc.get("error") or ph.get("error") or "",
    }


def audit_live_urls(targets: list[dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Audit live Office and Brand domains in parallel via page_checks, parse_html, agentic_check, sitemap_discovery."""
    from concurrent.futures import ThreadPoolExecutor

    targets = targets or LIVE_SITE_TARGETS
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(_audit_single_live_url, targets))


def sync_local_sitemap_and_llms() -> dict[str, Any]:
    """Task execution: keep Office robots.txt, sitemap.xml lastmod, and llms.txt accurate."""
    today = datetime.date.today().isoformat()
    actions: list[str] = []

    sitemap_path = OFFICE_DIR / "sitemap.xml"
    if sitemap_path.is_file():
        sm_txt = sitemap_path.read_text(encoding="utf-8")
        # Check newest HTML mtime in Office directory
        newest_date = today
        try:
            mtimes = [
                datetime.date.fromtimestamp((OFFICE_DIR / rel).stat().st_mtime).isoformat()
                for rel, _ in LOCAL_HTML_TARGETS
                if (OFFICE_DIR / rel).is_file()
            ]
            if mtimes:
                newest_date = max(mtimes)
        except OSError:
            pass
        updated_sm = re.sub(r"<lastmod>\d{4}-\d{2}-\d{2}</lastmod>", f"<lastmod>{newest_date}</lastmod>", sm_txt)
        if updated_sm != sm_txt:
            sitemap_path.write_text(updated_sm, encoding="utf-8", newline="\n")
            actions.append(f"Updated sitemap.xml <lastmod> to {newest_date}")

    robots_ok = (OFFICE_DIR / "robots.txt").is_file()
    llms_ok = (OFFICE_DIR / "llms.txt").is_file()
    return {
        "robots_txt_present": robots_ok,
        "sitemap_xml_present": sitemap_path.is_file(),
        "llms_txt_present": llms_ok,
        "actions_taken": actions,
    }


def generate_external_remediation_packs(live_results: list[dict[str, Any]]) -> list[str]:
    """For external domains missing robots/llms/meta/schema, create ready-to-deploy fix packs."""
    generated: list[str] = []
    for item in live_results:
        if item.get("role") not in ("brand_domain", "project_hub"):
            continue
        if item.get("error") and not item.get("status_code"):
            continue
        url = item["url"]
        host = (urlparse(url).netloc or "site").replace(":", "_")
        ag = item.get("agentic") or {}
        needs_llms = not ag.get("llms_found")
        needs_meta = not item.get("has_description") or not item.get("schema_count")
        if not (needs_llms or needs_meta):
            continue
        pack_dir = PACKS_DIR / host
        pack_dir.mkdir(parents=True, exist_ok=True)
        fix_md = [
            f"# SEO & GEO Remediation Pack for `{host}`",
            f"_Generated automatically by Office SEO Autopilot ({_local_stamp()})_",
            "",
            f"- Audited URL: {url}",
            f"- HTTP Status: {item.get('status_code')} | SEO Score: {item.get('score')}/100",
            f"- Missing items detected: {', '.join(item.get('top_findings') or ['meta/llms/schema'])}",
            "",
        ]
        if needs_llms:
            llms_body = (
                f"# {item.get('name') or host}\n\n"
                f"> Official digital platform at {url}.\n\n"
                f"## Key Pages\n\n"
                f"- [Home]({url}): Primary services, solutions, and contact hub.\n"
            )
            (pack_dir / "llms.txt").write_text(llms_body, encoding="utf-8", newline="\n")
            fix_md.append(f"- Created starter `{pack_dir / 'llms.txt'}` for AI search engines (GEO).")
        if needs_meta:
            snippet = (
                f'<meta name="description" content="{_html.escape(item.get("name") or host)} — official hub and services.">\n'
                f'<link rel="canonical" href="{url}">\n'
                '<script type="application/ld+json">\n'
                + json.dumps(
                    {
                        "@context": "https://schema.org",
                        "@type": "Organization",
                        "name": item.get("name") or host,
                        "url": url,
                    },
                    indent=2,
                )
                + "\n</script>\n"
            )
            (pack_dir / "head_seo_snippet.html").write_text(snippet, encoding="utf-8", newline="\n")
            fix_md.append(f"- Created `{pack_dir / 'head_seo_snippet.html'}` with meta description, canonical & JSON-LD.")
        (pack_dir / "README_SEO_FIX.md").write_text("\n".join(fix_md) + "\n", encoding="utf-8", newline="\n")
        generated.append(str(pack_dir / "README_SEO_FIX.md"))
    return generated


def queue_seo_task(description: str, url: str = "", priority: str = "high", source: str = "telegram") -> dict[str, Any]:
    """Enqueue an SEO task for immediate or startup execution."""
    q = _load_json(QUEUE_FILE, {"tasks": []})
    tasks = q.get("tasks") if isinstance(q, dict) else []
    if not isinstance(tasks, list):
        tasks = []
    task = {
        "id": f"seo-{int(time.time())}-{len(tasks) + 1}",
        "created_at": _utc_iso(),
        "description": str(description or "").strip()[:300],
        "url": str(url or "").strip(),
        "priority": priority,
        "source": source,
        "status": "pending",
    }
    tasks.append(task)
    _save_json(QUEUE_FILE, {"tasks": tasks[-100:]})
    return task


def _process_queued_tasks() -> list[dict[str, Any]]:
    q = _load_json(QUEUE_FILE, {"tasks": []})
    tasks = q.get("tasks") if isinstance(q, dict) else []
    if not isinstance(tasks, list):
        return []
    executed: list[dict[str, Any]] = []
    for t in tasks:
        if not isinstance(t, dict) or t.get("status") != "pending":
            continue
        url = (t.get("url") or "").strip()
        desc = (t.get("description") or "").strip()
        res_summary = ""
        if url:
            audit_res = audit_live_urls([{"name": desc or url, "url": url, "root": f"{urlparse(url).scheme}://{urlparse(url).netloc}", "role": "queued"}])
            r0 = audit_res[0] if audit_res else {}
            res_summary = (
                f"Audited {url}: HTTP {r0.get('status_code')}, score={r0.get('score')}/100, "
                f"findings={','.join(r0.get('top_findings') or ['none'])}"
            )
        else:
            sm = sync_local_sitemap_and_llms()
            res_summary = f"Local Office SEO maintenance completed ({len(sm.get('actions_taken') or [])} updates)"
        t["status"] = "completed"
        t["completed_at"] = _utc_iso()
        t["result_summary"] = res_summary
        executed.append(t)

    # Replenish next autonomous task if queue has no pending items so SEO always has next actions
    if not any(isinstance(x, dict) and x.get("status") == "pending" for x in tasks):
        tasks.append(
            {
                "id": f"seo-auto-{int(time.time())}",
                "created_at": _utc_iso(),
                "description": "Scheduled startup SEO + GEO drift audit across Office & Brand domains",
                "url": "",
                "priority": "normal",
                "source": "autopilot",
                "status": "pending",
            }
        )
    _save_json(QUEUE_FILE, {"tasks": tasks[-60:]})
    return executed


def update_drift_history(local_res: list[dict[str, Any]], live_res: list[dict[str, Any]]) -> dict[str, Any]:
    """Record SEO scores & detect any score regression against the prior run."""
    hist = _load_json(DRIFT_FILE, {"runs": []})
    runs = hist.get("runs") if isinstance(hist, dict) else []
    if not isinstance(runs, list):
        runs = []
    prev = runs[-1] if runs else {}
    prev_scores = prev.get("live_scores") or {}

    cur_live_scores = {x["url"]: x["score"] for x in live_res if x.get("url")}
    cur_local_scores = {x["file"]: x["score"] for x in local_res if x.get("file")}
    drifts: list[str] = []
    for u, s in cur_live_scores.items():
        if u in prev_scores and s < int(prev_scores[u]):
            drifts.append(f"{u}: {prev_scores[u]} -> {s}")

    run_entry = {
        "ts": _utc_iso(),
        "local_avg": round(sum(cur_local_scores.values()) / max(1, len(cur_local_scores)), 1),
        "live_avg": round(sum(cur_live_scores.values()) / max(1, len(cur_live_scores)), 1),
        "local_scores": cur_local_scores,
        "live_scores": cur_live_scores,
        "drifts": drifts,
    }
    runs.append(run_entry)
    _save_json(DRIFT_FILE, {"runs": runs[-30:]})
    return run_entry


def write_markdown_report(
    trigger: str,
    doctor_res: dict[str, Any],
    sm_sync: dict[str, Any],
    queued_done: list[dict[str, Any]],
    local_res: list[dict[str, Any]],
    live_res: list[dict[str, Any]],
    packs: list[str],
    drift: dict[str, Any],
) -> Path:
    INBOX_SEO_DIR.mkdir(parents=True, exist_ok=True)
    day_slug = datetime.date.today().strftime("%Y-%m-%d")
    report_path = INBOX_SEO_DIR / f"SEO_Startup_Report_{day_slug}.md"
    latest_path = INBOX_SEO_DIR / "LATEST_SEO_REPORT.md"

    manifest = _load_json(MANIFEST_FILE, {})
    skills_cnt = int(manifest.get("skills_count") or 0)
    agents_cnt = int(manifest.get("agents_count") or 0)

    lines = [
        f"# SEO-Автопилот Global Spirit Group ({_local_stamp()})",
        "",
        f"- **Триггер запуска:** `{trigger}`",
        f"- **Claude SEO (`AgriciDaniel/claude-seo`):** ready=`{doctor_res.get('ready')}` | skills=`{skills_cnt}` | subagents=`{agents_cnt}`",
        f"- **Средний балл локальных страниц Офиса:** **{drift.get('local_avg', 0)}/100**",
        f"- **Средний балл живых доменов:** **{drift.get('live_avg', 0)}/100**",
        f"- **Отслеживание просадок (Drift):** {', '.join(drift.get('drifts') or ['Без просадок'])}",
        "",
        "## 1. Выполненные задачи SEO-очереди и автообслуживание",
        f"- `robots.txt`: {'✅' if sm_sync.get('robots_txt_present') else '❌'} | `sitemap.xml`: {'✅' if sm_sync.get('sitemap_xml_present') else '❌'} | `llms.txt`: {'✅' if sm_sync.get('llms_txt_present') else '❌'}",
    ]
    for act in sm_sync.get("actions_taken") or []:
        lines.append(f"- Авто-фикc: {act}")
    for qt in queued_done:
        lines.append(f"- Задача `{qt.get('id')}`: {qt.get('description')} -> **{qt.get('result_summary')}**")
    if not queued_done:
        lines.append("- Пользовательских задач в очереди не было; выполнен полный цикл плановых проверок.")

    lines.extend(["", "## 2. Аудит локальных HTML-страниц Виртуального Офиса"])
    for r in local_res:
        schema_str = ", ".join(str(x) for x in (r.get("schema_types") or [])) or "none"
        lines.append(
            f"- `{r['file']}` -> **{r['score']}/100** | meta=`{r['has_description']}` | canonical=`{r['has_canonical']}` | og=`{r['has_og']}` | schema=`{schema_str}`"
        )

    lines.extend(["", "## 3. Аудит живых доменов (HTTP + Agentic GEO + Sitemap)"])
    for r in live_res:
        ag = r.get("agentic") or {}
        sm = r.get("sitemap") or {}
        note = r.get("error") or ", ".join(r.get("top_findings") or ["clean"])
        lines.append(
            f"- **{r['name']}** (`{r['url']}`) -> HTTP `{r.get('status_code')}` | Score **{r['score']}/100** | "
            f"robots=`{ag.get('robots_found')}` | llms=`{ag.get('llms_found')}` | sitemaps=`{sm.get('count', 0)}` | {note[:140]}"
        )

    if packs:
        lines.extend(["", "## 4. Подготовленные пакеты доработок для внешних доменов"])
        for p in packs:
            lines.append(f"- `{p}`")

    md_text = "\n".join(lines) + "\n"
    report_path.write_text(md_text, encoding="utf-8", newline="\n")
    latest_path.write_text(md_text, encoding="utf-8", newline="\n")
    return report_path


def send_telegram_seo_brief(
    local_res: list[dict[str, Any]],
    live_res: list[dict[str, Any]],
    queued_done: list[dict[str, Any]],
    report_path: Path,
    packs: list[str],
    drift: dict[str, Any],
) -> bool:
    cfg = _load_json(CONFIG_FILE, {})
    bot_tok = (os.environ.get("TG_BOT_TOKEN") or cfg.get("tg_bot_token") or "").strip()
    chat_id = str(os.environ.get("OWNER_TG_CHAT_ID") or cfg.get("owner_tg_chat_id") or "").strip()
    if not (bot_tok and chat_id):
        return False

    live_lines = []
    for r in live_res[:6]:
        st = r.get("status_code") or "err"
        live_lines.append(f"• <b>{_html.escape(r['name'][:30])}:</b> {r['score']}/100 (HTTP {st})")

    txt = (
        f"🔎 <b>SEO-АВТОПИЛОТ ОФИСА ЗАПУЩЕН ПЕРВЫМ ({_html.escape(_local_stamp()[:16])})</b>\n\n"
        f"• Локальные страницы Офиса: <b>{drift.get('local_avg', 0)}/100</b> ({len(local_res)} HTML)\n"
        f"• Живые домены (средний балл): <b>{drift.get('live_avg', 0)}/100</b>\n"
        f"• Выполнено задач очереди: <b>{len(queued_done)}</b> | Пакеты фиксов: <b>{len(packs)}</b>\n\n"
        + "\n".join(live_lines)
        + f"\n\n📄 Полный отчёт: <code>{_html.escape(str(report_path))}</code>"
    )
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": txt,
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": "🔎 Запустить /seo аудит сейчас", "callback_data": "cmd:seo"}],
                    [
                        {"text": "🕸️ Граф Агентов (Live)", "url": "https://globalteampartners.github.io/remote-agents-control/agents_live/"},
                        {"text": "🏢 Главный Офис", "url": "https://globalteampartners.github.io/remote-agents-control/?v=34.6"},
                    ],
                ]
            },
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_tok}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.status == 200
    except Exception as exc:
        _log(f"Telegram notification skipped/offline: {exc}")
        return False


def launch_office_poller_detached() -> None:
    """Start tg_bot_poller.py AFTER startup SEO phase finishes."""
    poller_py = OFFICE_DIR / "tg_bot_poller.py"
    if not poller_py.is_file():
        return
    pyw = Path(r"C:\Users\Vlaslav\AppData\Local\Programs\Python\Python312\pythonw.exe")
    exe = str(pyw if pyw.is_file() else _resolve_python_exe())
    flags = (0x00000008 | 0x00000200 | 0x08000000) if os.name == "nt" else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    try:
        subprocess.Popen(
            [exe, "-X", "utf8", "-u", str(poller_py), "--skip-startup-seo", "--log-file"],
            cwd=str(OFFICE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
        _log("Handed off cleanly to tg_bot_poller.py (--skip-startup-seo).")
    except OSError as exc:
        _log(f"Failed to start tg_bot_poller.py: {exc}")


def audit_and_process_office_tasks() -> dict[str, Any]:
    """Audit open/active Office tasks with installed Claude SEO skills & enrich GitHub issues."""
    try:
        import agents_live_aggregator
    except Exception as exc:
        return {"error": str(exc), "audited_urls": 0}

    cfg = _load_json(CONFIG_FILE, {})
    gtp_repo = (cfg.get("gtp_repo") or "GlobalTeamPartners/remote-agents-control").strip()
    gtp_tok = (cfg.get("gtp_token") or "").strip()

    snap = agents_live_aggregator.build_live_snapshot(include_remote=True)
    tasks = snap.get("tasks") or []

    # Collect target URLs from open GitHub issues & top pending website tasks
    target_items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for tk in tasks:
        if tk.get("source") == "seo_domain_health":
            continue
        t_url = str(tk.get("target_url") or tk.get("url") or "").strip()
        if not t_url or "github.com/" in t_url or "instagram.com" in t_url or "youtu" in t_url or "facebook.com" in t_url:
            continue
        clean_u = t_url.split("#")[0].rstrip(".,);\"'<>")
        if clean_u in seen_urls:
            continue
        if tk.get("status") in ("in_progress", "queued", "attention") or len(target_items) < 4:
            seen_urls.add(clean_u)
            target_items.append({"task": tk, "url": clean_u})
        if len(target_items) >= 5:
            break

    audited: list[dict[str, Any]] = []
    if target_items:
        live_targets = [
            {
                "name": x["task"]["id"],
                "url": x["url"],
                "root": f"{urlparse(x['url']).scheme}://{urlparse(x['url']).netloc}",
                "role": "office_task",
            }
            for x in target_items
        ]
        res_list = audit_live_urls(live_targets)
        for item, res in zip(target_items, res_list):
            tk = item["task"]
            audited.append({"task": tk, "audit": res})
            # Comment concise SEO Skill audit onto open GitHub issue once
            if tk.get("id", "").startswith("GH-") and tk.get("status") == "in_progress" and gtp_tok:
                issue_num = tk["id"].split("-", 1)[1]
                _post_seo_comment_if_missing(gtp_repo, gtp_tok, issue_num, tk, res)

    # Write detailed All Tasks + Claude SEO Skill Audit Markdown report
    INBOX_SEO_DIR.mkdir(parents=True, exist_ok=True)
    day_slug = datetime.date.today().strftime("%Y-%m-%d")
    task_rep = INBOX_SEO_DIR / f"All_Tasks_SEO_Audit_{day_slug}.md"
    latest_task_rep = INBOX_SEO_DIR / "LATEST_TASKS_SEO_AUDIT.md"
    lines = [
        f"# Аудит всех задач Офиса с новыми скиллами Claude SEO ({_local_stamp()})",
        "",
        f"- **Всего задач в едином реестре Офиса:** **{len(tasks)}**",
        f"- **В работе:** {snap['kpis'].get('tasks_in_progress', 0)} | **В очереди:** {snap['kpis'].get('tasks_queued', 0)} | **Требуют внимания:** {snap['kpis'].get('tasks_attention', 0)} | **Выполнено:** {snap['kpis'].get('tasks_done', 0)}",
        "",
        "## 1. Живая проверка веб-задач Офиса (Issue / Доска) через Claude SEO",
    ]
    for a in audited:
        tk = a["task"]
        r = a["audit"]
        ag = r.get("agentic") or {}
        sk_list = ", ".join(tk.get("skills") or [])
        top_f = ", ".join(r.get("top_findings") or ["clean"])
        lines.append(
            f"- **{tk['id']}** (`{r['url']}`) -> HTTP `{r.get('status_code')}` | Score **{r['score']}/100** | "
            f"robots=`{ag.get('robots_found')}` | llms=`{ag.get('llms_found')}` | Скиллы: `{sk_list}` | Находки: {top_f}"
        )
    if not audited:
        lines.append("- Открытых внешних веб-задач с доступными HTTP-сайтами не найдено.")

    lines.extend(["", "## 2. Матрица активных и приоритетных задач по агентам и SEO-скиллам"])
    for tk in tasks[:35]:
        lines.append(
            f"- `[{tk['status']}]` **{tk['id']}** ({tk['agent_id']}) · `{', '.join(tk.get('skills') or [])}` — {tk['title']}"
        )

    md = "\n".join(lines) + "\n"
    task_rep.write_text(md, encoding="utf-8", newline="\n")
    latest_task_rep.write_text(md, encoding="utf-8", newline="\n")
    return {"total_tasks": len(tasks), "audited_web_tasks": len(audited), "report_path": str(latest_task_rep)}


def _post_seo_comment_if_missing(repo: str, token: str, issue_num: str, tk: dict[str, Any], res: dict[str, Any]) -> None:
    hdr = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "GSG-Claude-SEO-Worker",
    }
    marker = "<!-- claude-seo-task-audit -->"
    try:
        with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/repos/{repo}/issues/{issue_num}/comments", headers=hdr), timeout=12) as cr:
            comments = json.loads(cr.read().decode("utf-8"))
        if any(marker in (c.get("body") or "") for c in comments):
            return
    except Exception:
        return

    ag = res.get("agentic") or {}
    sm = res.get("sitemap") or {}
    findings = ", ".join(res.get("top_findings") or ["нет критических замечаний"])
    skills_txt = ", ".join(f"`{s}`" for s in (tk.get("skills") or []))
    body_md = (
        f"🔎 **Claude SEO-Автопилот (`v34.6`): Авто-разбор открытой задачи #{issue_num}**\n\n"
        f"- **Целевой сайт:** {res.get('url')}\n"
        f"- **Назначенные скиллы:** {skills_txt}\n"
        f"- **HTTP / SEO Оценка:** `HTTP {res.get('status_code')}` · **{res.get('score')}/100**\n"
        f"- **Мета-теги / GEO:** Title=`{str(res.get('title') or '')[:80]}`, MetaDesc=`{res.get('has_description')}`, Canonical=`{res.get('has_canonical')}`, OpenGraph=`{res.get('has_og')}`, JSON-LD=`{res.get('schema_count')}`\n"
        f"- **Индексация ИИ-краулерами:** `robots.txt={ag.get('robots_found')}` · `llms.txt={ag.get('llms_found')}` · `sitemaps={sm.get('count', 0)}`\n"
        f"- **Основные находки:** {findings}\n\n"
        f"🕸️ Живой статус и граф агентов: https://globalteampartners.github.io/remote-agents-control/agents_live/\n\n"
        f"{marker}"
    )
    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/issues/{issue_num}/comments",
            data=json.dumps({"body": body_md}).encode("utf-8"),
            headers=hdr,
            method="POST",
        )
        urllib.request.urlopen(req, timeout=12).close()
    except Exception:
        pass


def run_seo_cycle(
    trigger: str = "manual",
    force: bool = False,
    notify: bool = True,
    cooldown_sec: int = DEFAULT_STARTUP_COOLDOWN_SEC,
    single_url: str = "",
) -> dict[str, Any]:
    if not acquire_seo_mutex():
        _log("Another SEO-FIRST worker is already running; skipping duplicate launch.")
        return {"skipped": True, "reason": "mutex_held"}

    state = _load_json(STATE_FILE, {})
    last_ts = float(state.get("last_run_epoch") or 0)
    q = _load_json(QUEUE_FILE, {"tasks": []})
    pending_count = sum(1 for t in (q.get("tasks") or []) if isinstance(t, dict) and t.get("status") == "pending")

    if not force and not single_url and pending_count == 0 and (time.time() - last_ts) < cooldown_sec:
        _log(f"Cooldown active ({int(time.time() - last_ts)}s < {cooldown_sec}s) and no urgent pending tasks.")
        return {"skipped": True, "reason": "cooldown", "last_run_iso": state.get("last_run_iso")}

    _log(f"Starting SEO autopilot cycle (trigger={trigger}, pending={pending_count})...")
    doctor_res = run_claude_seo_script("runtime.py", ["doctor", "--json"], timeout=15)
    sm_sync = sync_local_sitemap_and_llms()
    queued_done = _process_queued_tasks()
    local_res = audit_local_html_files()

    if single_url:
        host = urlparse(single_url).netloc or single_url
        live_res = audit_live_urls(
            [{"name": host, "url": single_url, "root": f"{urlparse(single_url).scheme}://{host}", "role": "on_demand"}]
        )
    else:
        live_res = audit_live_urls(LIVE_SITE_TARGETS)

    packs = generate_external_remediation_packs(live_res)
    drift = update_drift_history(local_res, live_res)
    office_tasks_res = audit_and_process_office_tasks() if not single_url else {"audited_web_tasks": 0}
    report_path = write_markdown_report(trigger, doctor_res, sm_sync, queued_done, local_res, live_res, packs, drift)

    summary_state = {
        "last_run_epoch": time.time(),
        "last_run_iso": _utc_iso(),
        "trigger": trigger,
        "claude_seo_ready": bool(doctor_res.get("ready")),
        "local_avg_score": drift.get("local_avg"),
        "live_avg_score": drift.get("live_avg"),
        "report_path": str(report_path),
        "tasks_audit_report": office_tasks_res.get("report_path") or "",
        "queued_completed": len(queued_done),
        "office_web_tasks_audited": office_tasks_res.get("audited_web_tasks", 0),
        "packs_generated": len(packs),
        "live_summary": [
            {"name": r["name"], "url": r["url"], "status_code": r.get("status_code"), "score": r["score"]}
            for r in live_res
        ],
    }
    _save_json(STATE_FILE, summary_state)

    try:
        import agents_live_aggregator
        snap = agents_live_aggregator.build_live_snapshot(include_remote=True)
        agents_live_aggregator.push_live_snapshot_to_github(snap)
        summary_state["live_graph_synced"] = True
    except Exception as _gl_err:
        _log(f"Live graph snapshot sync warn: {_gl_err}")
        summary_state["live_graph_synced"] = False

    tg_sent = False
    if notify:
        tg_sent = send_telegram_seo_brief(local_res, live_res, queued_done, report_path, packs, drift)
    summary_state["telegram_notified"] = tg_sent
    _log(
        f"SEO cycle complete: local={drift.get('local_avg')}/100, live={drift.get('live_avg')}/100, "
        f"tasks_done={len(queued_done)}, tg_sent={tg_sent}"
    )
    return summary_state


def format_status_for_telegram() -> str:
    state = _load_json(STATE_FILE, {})
    manifest = _load_json(MANIFEST_FILE, {})
    q = _load_json(QUEUE_FILE, {"tasks": []})
    pending = [t for t in (q.get("tasks") or []) if isinstance(t, dict) and t.get("status") == "pending"]
    live_rows = state.get("live_summary") or []
    rows_html = "\n".join(
        f"• <b>{_html.escape(str(r.get('name', ''))[:28])}:</b> {r.get('score', 0)}/100 (HTTP {r.get('status_code') or '?'})"
        for r in live_rows[:7]
    ) or "• <i>Первый скан запускается...</i>"
    return (
        "🔎 <b>OFFICE CLAUDE SEO AUTOPILOT (v34.5)</b>\n\n"
        f"• <b>Установлено скиллов:</b> {manifest.get('skills_count', 35)} | <b>Сабагентов:</b> {manifest.get('agents_count', 21)}\n"
        f"• <b>Автозапуск первым при включении ПК:</b> ✅ Активен\n"
        f"• <b>Последний цикл:</b> <code>{_html.escape(str(state.get('last_run_iso') or 'ещё не запускался'))}</code>\n"
        f"• <b>Средний балл (Локал / Домены):</b> <b>{state.get('local_avg_score', '?')}/100</b> · <b>{state.get('live_avg_score', '?')}/100</b>\n"
        f"• <b>В очереди задач:</b> {len(pending)}\n\n"
        f"{rows_html}\n\n"
        "<i>Команды:</i> <code>/seo</code> (запустить сейчас) · <code>/seo https://site.com</code> (аудит URL) · <code>/seo задача: текст</code>"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Global Spirit Group Autonomous Startup-First SEO Worker")
    parser.add_argument("--startup", action="store_true", help="Run priority computer-boot SEO phase first")
    parser.add_argument("--run-now", action="store_true", help="Force immediate SEO audit + tasks cycle")
    parser.add_argument("--audit", default="", help="Audit a specific URL immediately")
    parser.add_argument("--task", default="", help="Enqueue and immediately execute an SEO task")
    parser.add_argument("--quiet", action="store_true", help="Do not send Telegram summary")
    parser.add_argument("--then-start-poller", action="store_true", help="Launch tg_bot_poller.py after SEO completes")
    parser.add_argument("--json", action="store_true", help="Print JSON result")
    args = parser.parse_args()

    try:
        if args.task:
            urls = re.findall(r"https?://[^\s<>'\"]+", args.task)
            queue_seo_task(args.task, url=urls[0] if urls else "", priority="high", source="cli")
            res = run_seo_cycle(trigger="cli_task", force=True, notify=not args.quiet)
        elif args.audit:
            res = run_seo_cycle(trigger="cli_audit", force=True, notify=not args.quiet, single_url=args.audit)
        elif args.startup:
            res = run_seo_cycle(trigger="windows_startup", force=args.run_now, notify=not args.quiet)
        else:
            res = run_seo_cycle(trigger="manual", force=args.run_now or True, notify=not args.quiet)

        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    finally:
        if args.then_start_poller:
            launch_office_poller_detached()


if __name__ == "__main__":
    raise SystemExit(main())
