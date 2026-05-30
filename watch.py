#!/usr/bin/env python3
"""
Live monitor for waibee background jobs.

Usage:
    python watch.py              # all active jobs (running + finished last 5 min)
    python watch.py <job_id>     # specific job, exits when done
"""
import sys
import time
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "waibee.db"
POLL = 1.0  # seconds between refreshes

# ANSI
G = "\033[92m"   # green
Y = "\033[93m"   # yellow
R = "\033[91m"   # red
C = "\033[96m"   # cyan
GR = "\033[90m"  # gray
B = "\033[1m"    # bold
RST = "\033[0m"
CLR = "\033[2J\033[H"


def _con():
    con = sqlite3.connect(DB_PATH, timeout=3)
    con.row_factory = sqlite3.Row
    return con


def get_jobs(job_id=None):
    with _con() as con:
        if job_id:
            rows = con.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchall()
        else:
            cutoff = time.time() - 300
            rows = con.execute(
                "SELECT * FROM jobs WHERE status='running' OR updated_at > ? "
                "ORDER BY created_at DESC LIMIT 15",
                (cutoff,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_trace(job_id, limit=10):
    with _con() as con:
        rows = con.execute(
            "SELECT * FROM trace WHERE job_id=? ORDER BY ts DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
    return list(reversed([dict(r) for r in rows]))


def status_color(s):
    return {
        "running": C,
        "done": G,
        "failed": R,
        "timeout": R,
        "cancelled": R,
        "interrupted": Y,
    }.get(s, GR)


def elapsed(ts):
    s = int(time.time() - ts)
    return f"{s//60}m{s%60:02d}s" if s >= 60 else f"{s}s"


def tool_color(tool, result):
    if "TIMEOUT" in result or "BLOCKED" in result or "ERROR" in result:
        return R
    if tool == "write_file":
        return G
    if tool in ("read_file", "glob_search", "grep_search", "list_dir"):
        return Y
    return GR


def render(jobs):
    if not jobs:
        return f"{GR}no active jobs{RST}\n"
    lines = []
    for j in jobs:
        sc = status_color(j["status"])
        bullet = "●" if j["status"] == "running" else "○"
        lines.append(
            f"{B}{sc}{bullet} {j['job_id']}  {j['status']}{RST}"
            f"  {GR}{elapsed(j['created_at'])}  step {j['last_step']}"
            f"  ${j['cost']:.4f}{RST}"
        )
        task = (j["task"] or "")[:70]
        lines.append(f"  {GR}{task}{RST}")

        for t in get_trace(j["job_id"], limit=8):
            tc = tool_color(t["tool"], t["result_preview"])
            ts = datetime.fromtimestamp(t["ts"]).strftime("%H:%M:%S")
            args = t["args_preview"][:35].replace("\n", "↵")
            res = t["result_preview"][:45].replace("\n", "↵")
            lines.append(
                f"  {GR}{ts}{RST}  {tc}{t['tool']:<14}{RST}"
                f"  {args:<35}  {GR}→ {res}{RST}"
            )

        if j["status"] == "done" and j["result"]:
            lines.append(f"  {G}✓ {j['result'][:80]}{RST}")
        elif j["error"]:
            lines.append(f"  {R}✗ {j['error'][:80]}{RST}")

        lines.append("")
    return "\n".join(lines)


def main():
    filter_id = sys.argv[1] if len(sys.argv) > 1 else None

    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        sys.exit(1)

    try:
        while True:
            jobs = get_jobs(filter_id)
            now = datetime.now().strftime("%H:%M:%S")
            sys.stdout.write(
                CLR + f"{B}waibee watch{RST}  {GR}{now}{RST}"
                + (f"  {filter_id}" if filter_id else "")
                + "  (Ctrl+C to quit)\n\n"
            )
            sys.stdout.write(render(jobs))
            sys.stdout.flush()

            if filter_id and jobs and all(j["status"] != "running" for j in jobs):
                print(f"\n{G}Job finished. Ctrl+C to exit.{RST}")
                time.sleep(2)
                break

            time.sleep(POLL)
    except KeyboardInterrupt:
        print(f"\n{GR}bye{RST}")


if __name__ == "__main__":
    main()
