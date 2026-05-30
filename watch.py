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

# ANSI colors
G   = "\033[92m"   # green      — write_file, done
Y   = "\033[93m"   # yellow     — read ops
R   = "\033[91m"   # red        — errors / timeout
C   = "\033[96m"   # cyan       — running status
M   = "\033[95m"   # magenta    — bash_run
W   = "\033[97m"   # white      — reasoning text
GR  = "\033[90m"   # gray       — timestamps, metadata
B   = "\033[1m"    # bold
DIM = "\033[2m"    # dim
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
            cutoff = time.time() - 30  # done jobs visible 30s
            rows = con.execute(
                "SELECT * FROM jobs WHERE status='running' OR updated_at > ? "
                "ORDER BY created_at DESC LIMIT 15",
                (cutoff,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_activity(job_id, limit=12):
    """Merge assistant reasoning + tool calls, sorted by time."""
    with _con() as con:
        msg_rows = con.execute(
            "SELECT ts, data FROM messages WHERE job_id=? AND role='assistant' "
            "ORDER BY ts DESC LIMIT ?",
            (job_id, limit * 2),
        ).fetchall()
        trace_rows = con.execute(
            "SELECT ts, tool, args_preview, result_preview FROM trace "
            "WHERE job_id=? ORDER BY ts DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()

    items = []

    for r in msg_rows:
        try:
            data = json.loads(r["data"])
        except Exception:
            continue
        content = data.get("content", "")
        if content and isinstance(content, str) and content.strip():
            items.append({"type": "reasoning", "ts": r["ts"], "text": content.strip()})

    for r in trace_rows:
        items.append({
            "type": "tool",
            "ts": r["ts"],
            "tool": r["tool"],
            "args": r["args_preview"],
            "result": r["result_preview"],
        })

    items.sort(key=lambda x: x["ts"])
    return items[-limit:]


def status_color(s):
    return {"running": C, "done": G, "failed": R, "timeout": R,
            "cancelled": R, "interrupted": Y}.get(s, GR)


def elapsed(ts):
    s = int(time.time() - ts)
    return f"{s//60}m{s%60:02d}s" if s >= 60 else f"{s}s"


def tool_color(tool, result):
    if any(x in result for x in ("TIMEOUT", "BLOCKED", "ERROR")):
        return R
    return {
        "write_file": G,
        "read_file":  Y,
        "glob_search": Y,
        "grep_search": Y,
        "list_dir":   Y,
        "bash_run":   M,
    }.get(tool, GR)


def fmt_reasoning(text, width=72):
    """Wrap and indent reasoning text."""
    words = text.replace("\n", " ").split()
    lines, cur = [], []
    cur_len = 0
    for w in words:
        if cur_len + len(w) + 1 > width:
            lines.append(" ".join(cur))
            cur, cur_len = [w], len(w)
        else:
            cur.append(w)
            cur_len += len(w) + 1
    if cur:
        lines.append(" ".join(cur))
    return lines


def render(jobs):
    if not jobs:
        return f"{GR}no active jobs{RST}\n"
    out = []
    for j in jobs:
        sc = status_color(j["status"])
        bullet = "●" if j["status"] == "running" else "○"
        out.append(
            f"{B}{sc}{bullet} {j['job_id']}  {j['status']}{RST}"
            f"  {GR}{elapsed(j['created_at'])}  step {j['last_step']}  ${j['cost']:.4f}{RST}"
        )
        task = (j["task"] or "")[:72]
        out.append(f"  {DIM}{task}{RST}")
        out.append("")

        for item in get_activity(j["job_id"], limit=10):
            ts = datetime.fromtimestamp(item["ts"]).strftime("%H:%M:%S")

            if item["type"] == "reasoning":
                # Show first line of reasoning + continuation lines
                wrapped = fmt_reasoning(item["text"])
                prefix = f"  {GR}{ts}{RST}  {W}💭 {RST}"
                blank  = f"  {GR}        {RST}     "
                out.append(f"{prefix}{W}{wrapped[0][:72]}{RST}")
                for wl in wrapped[1:3]:  # max 3 lines of reasoning
                    out.append(f"{blank}{GR}{wl[:72]}{RST}")

            else:
                tc = tool_color(item["tool"], item["result"])
                args = item["args"][:32].replace("\n", "↵")
                res  = item["result"][:48].replace("\n", "↵")
                icon = {"write_file": "✏ ", "read_file": "📄",
                        "bash_run": "⚡", "glob_search": "🔍",
                        "grep_search": "🔍", "list_dir": "📁"}.get(item["tool"], "🔧")
                out.append(
                    f"  {GR}{ts}{RST}  {tc}{icon} {item['tool']:<13}{RST}"
                    f"  {GR}{args:<32}{RST}  {GR}→ {res}{RST}"
                )

        if j["status"] == "done" and j["result"]:
            out.append(f"\n  {G}✓ {j['result'][:80]}{RST}")
        elif j["error"]:
            out.append(f"\n  {R}✗ {j['error'][:80]}{RST}")

        out.append("\n")
    return "\n".join(out)


def _state_key(jobs):
    return tuple(
        (j["job_id"], j["status"], j["last_step"], round(j["cost"], 4))
        for j in jobs
    )


def main():
    filter_id = sys.argv[1] if len(sys.argv) > 1 else None

    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        sys.exit(1)

    last_key = None
    last_activity = time.time()

    try:
        while True:
            jobs = get_jobs(filter_id)
            key = _state_key(jobs)
            now_str = datetime.now().strftime("%H:%M:%S")

            if key != last_key:
                last_key = key
                last_activity = time.time()
                sys.stdout.write(
                    CLR + f"{B}waibee watch{RST}  {GR}{now_str}{RST}"
                    + (f"  {GR}{filter_id}{RST}" if filter_id else "")
                    + "  (Ctrl+C to quit)\n\n"
                )
                sys.stdout.write(render(jobs))
                sys.stdout.flush()
            else:
                idle = int(time.time() - last_activity)
                idle_str = f"{idle//60}m{idle%60:02d}s" if idle >= 60 else f"{idle}s"
                sys.stdout.write(
                    f"\r\033[1A\033[2K"
                    f"{B}waibee watch{RST}  {GR}{now_str}{RST}"
                    + (f"  {GR}{filter_id}{RST}" if filter_id else "")
                    + f"  {GR}· thinking {idle_str}{RST}  (Ctrl+C to quit)"
                )
                sys.stdout.flush()

            if filter_id and jobs and all(j["status"] != "running" for j in jobs):
                print(f"\n\n{G}✓ done{RST}")
                time.sleep(2)
                break
            if not filter_id and not any(j["status"] == "running" for j in jobs) and last_key is not None:
                print(f"\n\n{GR}no running jobs — exiting{RST}")
                time.sleep(1)
                break

            time.sleep(POLL)
    except KeyboardInterrupt:
        print(f"\n{GR}bye{RST}")


if __name__ == "__main__":
    main()
