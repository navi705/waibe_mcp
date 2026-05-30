#!/usr/bin/env python3
"""
Live monitor for waibee background jobs.

Usage:
    python watch.py              # all active jobs
    python watch.py <job_id>     # specific job, exits when done
"""
import json
import sys
import time
import sqlite3
from datetime import datetime
from pathlib import Path

from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

try:
    import msvcrt
    def _read_keys():
        keys = []
        while msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch == b"\xe0":
                ch2 = msvcrt.getch()
                if ch2 == b"H": keys.append("up")
                elif ch2 == b"P": keys.append("down")
                elif ch2 == b"I": keys.append("pgup")
                elif ch2 == b"Q": keys.append("pgdn")
            elif ch in (b"q", b"Q"):
                keys.append("quit")
        return keys
except ImportError:
    def _read_keys(): return []

DB_PATH = Path(__file__).parent / "waibee.db"
POLL = 1.0
ACTIVITY_LIMIT = 5
KEY_INTERVAL = 0.05


def _con():
    con = sqlite3.connect(DB_PATH, timeout=3)
    con.row_factory = sqlite3.Row
    return con


DONE_TTL = 120  # seconds before completed jobs disappear

def get_jobs(job_id=None):
    with _con() as con:
        if job_id:
            rows = con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchall()
        else:
            cutoff = time.time() - DONE_TTL
            rows = con.execute(
                "SELECT * FROM jobs "
                "WHERE status IN ('running', 'interrupted') OR updated_at > ? "
                "ORDER BY created_at DESC LIMIT 20",
                (cutoff,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_activity(job_id):
    with _con() as con:
        msg_rows = con.execute(
            "SELECT ts, data FROM messages WHERE job_id=? AND role='assistant' "
            "ORDER BY ts DESC LIMIT ?",
            (job_id, ACTIVITY_LIMIT * 2),
        ).fetchall()
        trace_rows = con.execute(
            "SELECT ts, tool, args_preview, result_preview FROM trace "
            "WHERE job_id=? ORDER BY ts DESC LIMIT ?",
            (job_id, ACTIVITY_LIMIT),
        ).fetchall()

    items = []
    for r in msg_rows:
        try:
            data = json.loads(r["data"])
        except Exception:
            continue
        content = data.get("content", "")
        if content and isinstance(content, str) and content.strip():
            items.append({
                "type": "think", "ts": r["ts"],
                "text": content.strip().replace("\n", " "),
            })
    for r in trace_rows:
        items.append({
            "type": "tool", "ts": r["ts"],
            "tool": r["tool"], "args": r["args_preview"], "result": r["result_preview"],
        })

    items.sort(key=lambda x: x["ts"])
    return items[-ACTIVITY_LIMIT:]


def elapsed(ts):
    s = int(time.time() - ts)
    if s < 60:   return f"{s}s"
    if s < 3600: return f"{s//60}m{s%60:02d}s"
    return f"{s//3600}h{(s%3600)//60}m"


TOOL_ICON = {
    "write_file": "✏ ", "read_file": "📄", "bash_run": "⚡",
    "glob_search": "🔍", "grep_search": "🔍", "list_dir": "📁",
}


def tool_style(tool, result):
    if any(x in result for x in ("TIMEOUT", "BLOCKED", "ERROR")):
        return "red"
    return {
        "write_file": "green", "read_file": "yellow",
        "glob_search": "yellow", "grep_search": "yellow",
        "list_dir": "yellow", "bash_run": "magenta",
    }.get(tool, "dim")


def render_job(j) -> Panel:
    status = j["status"]
    sc = {"running": "cyan", "done": "green", "failed": "red",
          "timeout": "red", "cancelled": "red", "interrupted": "yellow"}.get(status, "dim")
    bullet = "●" if status == "running" else "○"

    title = Text()
    title.append(f"{bullet} {j['job_id']}  ", style=f"bold {sc}")
    title.append(status, style=sc)
    title.append(f"  {elapsed(j['created_at'])}  step {j['last_step']}  ${j['cost']:.4f}", style="dim")

    task_line = Text((j["task"] or "")[:80], style="dim")

    # Running jobs: show up to ACTIVITY_LIMIT rows. Done/failed: show only last 2 (footer is the focus).
    is_running = status == "running"
    activity = get_activity(j["job_id"])
    if not is_running:
        activity = activity[-2:]
    last_ts = activity[-1]["ts"] if activity else j["created_at"]

    tbl = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    tbl.add_column("time", style="dim", width=9, no_wrap=True)
    tbl.add_column("icon", width=2, no_wrap=True)
    tbl.add_column("tool", width=14, no_wrap=True)
    tbl.add_column("detail")

    for item in activity:
        ts = datetime.fromtimestamp(item["ts"]).strftime("%H:%M:%S")
        if item["type"] == "think":
            text = item["text"][:200] + ("…" if len(item["text"]) > 200 else "")
            tbl.add_row(ts, "💭", "", Text(text, style="white"))
        else:
            style = tool_style(item["tool"], item["result"])
            icon = TOOL_ICON.get(item["tool"], "🔧")
            args_display = item["args"][:100].replace("\n", " ") + ("…" if len(item["args"]) > 100 else "")
            result_display = item["result"][:150].replace("\n", " ") + ("…" if len(item["result"]) > 150 else "")
            detail = Text()
            detail.append(f"{args_display}  → ", style="dim")
            detail.append(result_display, style="dim")
            tbl.add_row(ts, icon, Text(item["tool"], style=style), detail)

    # Footer
    if status == "running":
        footer = Text(f"⏳ thinking {elapsed(last_ts)}...", style="cyan")
    elif status == "done":
        footer = Text(f"✓ {(j['result'] or '')[:200].replace(chr(10), ' ')}", style="green")
    elif status in ("failed", "timeout"):
        footer = Text(f"✗ {(j['error'] or status)[:200]}", style="red")
    elif status == "interrupted":
        footer = Text(f'⚠ interrupted — waibee_resume("{j["job_id"]}")', style="yellow")
    else:
        footer = Text(status, style="dim")

    return Panel(Group(task_line, tbl, footer), title=title, title_align="left",
                 border_style=sc, box=box.SIMPLE)


def build_display(filter_id, scroll_offset=0):
    now = datetime.now().strftime("%H:%M:%S")
    header = Text()
    header.append("waibee watch", style="bold")
    header.append(f"  {now}", style="dim")
    if filter_id:
        header.append(f"  {filter_id}", style="dim")
    header.append("  ↑↓ scroll  q quit", style="dim")

    jobs = get_jobs(filter_id)
    if not jobs:
        return Group(header, Text(""), Text("waiting for jobs...", style="dim")), jobs, 0

    total = len(jobs)
    scroll_offset = max(0, min(scroll_offset, total - 1))
    visible = jobs[scroll_offset:]

    parts = [header, Text("")]
    if scroll_offset > 0:
        parts.append(Text(f"  ↑ {scroll_offset} more above", style="dim"))
    parts.extend(render_job(j) for j in visible)
    return Group(*parts), jobs, scroll_offset


def main():
    filter_id = sys.argv[1] if len(sys.argv) > 1 else None

    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        sys.exit(1)

    scroll_offset = 0
    last_db_refresh = 0.0
    jobs: list = []

    console = Console()
    with Live(console=console, refresh_per_second=25, screen=True) as live:
        try:
            while True:
                # Keys — every 50ms
                dirty = False
                for key in _read_keys():
                    if key == "quit":
                        return
                    elif key == "up":   scroll_offset = max(0, scroll_offset - 1); dirty = True
                    elif key == "down": scroll_offset += 1; dirty = True
                    elif key == "pgup": scroll_offset = max(0, scroll_offset - 3); dirty = True
                    elif key == "pgdn": scroll_offset += 3; dirty = True

                # DB — every 1s
                now = time.monotonic()
                if now - last_db_refresh >= POLL:
                    last_db_refresh = now
                    dirty = True

                if dirty:
                    display, jobs, scroll_offset = build_display(filter_id, scroll_offset)
                    live.update(display)

                if filter_id and jobs and all(j["status"] != "running" for j in jobs):
                    time.sleep(2)
                    break

                time.sleep(KEY_INTERVAL)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
