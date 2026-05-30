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

DB_PATH = Path(__file__).parent / "waibee.db"
POLL = 1.0

# ANSI
G   = "\033[92m"   # green   — write_file, done
Y   = "\033[93m"   # yellow  — read ops
R   = "\033[91m"   # red     — errors
C   = "\033[96m"   # cyan    — running
M   = "\033[95m"   # magenta — bash_run
W   = "\033[97m"   # white   — reasoning
GR  = "\033[90m"   # gray    — meta
B   = "\033[1m"
DIM = "\033[2m"
RST = "\033[0m"
CLR = "\033[2J\033[H"

ACTIVITY_LIMIT = 10  # last N items shown per job


def _con():
    con = sqlite3.connect(DB_PATH, timeout=3)
    con.row_factory = sqlite3.Row
    return con


def get_jobs(job_id=None):
    with _con() as con:
        if job_id:
            rows = con.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchall()
        else:
            cutoff = time.time() - 30
            rows = con.execute(
                "SELECT * FROM jobs WHERE status='running' OR updated_at > ? "
                "ORDER BY created_at DESC LIMIT 10",
                (cutoff,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_activity(job_id):
    """Merge assistant reasoning + tool trace, newest-last, limited."""
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
            items.append({"type": "think", "ts": r["ts"],
                          "text": content.strip().replace("\n", " ")})

    for r in trace_rows:
        items.append({"type": "tool", "ts": r["ts"],
                      "tool": r["tool"],
                      "args": r["args_preview"],
                      "result": r["result_preview"]})

    items.sort(key=lambda x: x["ts"])
    return items[-ACTIVITY_LIMIT:]


def elapsed(ts):
    s = int(time.time() - ts)
    if s < 60:   return f"{s}s"
    if s < 3600: return f"{s//60}m{s%60:02d}s"
    return f"{s//3600}h{(s%3600)//60}m"


def tool_color(tool, result):
    if any(x in result for x in ("TIMEOUT", "BLOCKED", "ERROR")):
        return R
    return {"write_file": G, "read_file": Y, "glob_search": Y,
            "grep_search": Y, "list_dir": Y, "bash_run": M}.get(tool, GR)


TOOL_ICON = {"write_file": "✏ ", "read_file": "📄", "bash_run": "⚡",
             "glob_search": "🔍", "grep_search": "🔍", "list_dir": "📁"}


def wrap(text, width=65):
    words = text.split()
    lines, cur, cur_len = [], [], 0
    for w in words:
        if cur_len + len(w) + 1 > width:
            lines.append(" ".join(cur))
            cur, cur_len = [w], len(w)
        else:
            cur.append(w); cur_len += len(w) + 1
    if cur: lines.append(" ".join(cur))
    return lines or [""]


def render_job(j):
    lines = []
    now = time.time()

    # ── Header ──────────────────────────────────────────────────────────
    status = j["status"]
    sc = {"running": C, "done": G, "failed": R, "timeout": R,
          "cancelled": R, "interrupted": Y}.get(status, GR)
    bullet = "●" if status == "running" else "○"
    lines.append(
        f"{B}{sc}{bullet} {j['job_id']}  {status}{RST}"
        f"  {GR}{elapsed(j['created_at'])}  step {j['last_step']}  ${j['cost']:.4f}{RST}"
    )

    # ── Task ────────────────────────────────────────────────────────────
    task = (j["task"] or "")[:72]
    lines.append(f"  {DIM}{task}{RST}")
    lines.append(f"  {GR}{'─' * 70}{RST}")

    # ── Activity ────────────────────────────────────────────────────────
    activity = get_activity(j["job_id"])
    last_ts = activity[-1]["ts"] if activity else j["created_at"]

    for item in activity:
        ts = datetime.fromtimestamp(item["ts"]).strftime("%H:%M:%S")

        if item["type"] == "think":
            wrapped = wrap(item["text"])
            lines.append(f"  {GR}{ts}{RST}  {W}💭 {wrapped[0][:65]}{RST}")
            for wl in wrapped[1:2]:
                lines.append(f"           {GR}   {wl[:65]}{RST}")
        else:
            tc = tool_color(item["tool"], item["result"])
            icon = TOOL_ICON.get(item["tool"], "🔧")
            args = item["args"][:28].replace("\n", "↵")
            res  = item["result"][:40].replace("\n", "↵")
            lines.append(
                f"  {GR}{ts}{RST}  {tc}{icon} {item['tool']:<13}{RST}"
                f"  {GR}{args:<28}  → {res}{RST}"
            )

    # ── Status line ─────────────────────────────────────────────────────
    lines.append(f"  {GR}{'─' * 70}{RST}")
    if status == "running":
        idle = int(now - last_ts)
        idle_str = elapsed(last_ts)
        lines.append(f"  {C}⏳ thinking {idle_str}...{RST}")
    elif status == "done":
        result = (j["result"] or "")
        short = result[:80].replace("\n", " ")
        lines.append(f"  {G}✓ {short}{RST}")
    elif status in ("failed", "timeout"):
        err = (j["error"] or "")[:80]
        lines.append(f"  {R}✗ {err or status}{RST}")
    elif status == "interrupted":
        lines.append(f"  {Y}⚠ interrupted — call waibee_resume(\"{j['job_id']}\"){RST}")

    lines.append("")
    return lines


def render(jobs):
    if not jobs:
        return [f"{GR}no active jobs{RST}", ""]
    out = []
    for j in jobs:
        out.extend(render_job(j))
    return out


def main():
    filter_id = sys.argv[1] if len(sys.argv) > 1 else None

    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        sys.exit(1)

    started = False
    try:
        while True:
            jobs = get_jobs(filter_id)
            now_str = datetime.now().strftime("%H:%M:%S")

            header = (
                f"{B}waibee watch{RST}  {GR}{now_str}{RST}"
                + (f"  {GR}{filter_id}{RST}" if filter_id else "")
                + "  (Ctrl+C to quit)"
            )

            lines = [header, ""] + render(jobs)
            sys.stdout.write(CLR + "\n".join(lines))
            sys.stdout.flush()
            started = True

            # Exit conditions
            if filter_id and jobs and all(j["status"] != "running" for j in jobs):
                sys.stdout.write(f"\n\n{G}✓ done — exiting{RST}\n")
                sys.stdout.flush()
                time.sleep(2)
                break
            if not filter_id and started and not any(j["status"] == "running" for j in jobs):
                sys.stdout.write(f"\n\n{GR}no running jobs — exiting{RST}\n")
                sys.stdout.flush()
                time.sleep(1)
                break

            time.sleep(POLL)
    except KeyboardInterrupt:
        print(f"\n{GR}bye{RST}")


if __name__ == "__main__":
    main()
