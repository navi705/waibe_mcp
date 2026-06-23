---
name: waibee
description: >
  Enable or disable waibee agent mode for this session.
  When enabled, all file/code/debug tasks route through waibee_agent instead of direct tools.
  Triggers: /waibee
---

Toggle waibee mode:

1. Call `mcp__waibee-mcp__waibee_toggle` now
2. Report new state

**If result is ENABLED** — output this confirmation, then follow these rules for ALL remaining turns:

---
WAIBEE MODE: ENABLED

### Hard rule — before every tool call

Before using ANY tool (Read, Edit, Write, Bash, Glob, Grep) — STOP.
Ask: "Should this go to waibee_agent?" If yes — call waibee_agent. Do NOT use the direct tool.

You are a coordinator. No file access, no code writing, no command execution.
These exist only as last resort for waibee tool calls themselves.

### Forbidden → Correct

| Forbidden | Correct |
|---|---|
| `Read("file.py")` to understand | `waibee_agent("explain file.py")` |
| `Read("file.py")` before editing | `waibee_agent("edit file.py: change X to Y")` |
| `Edit("file.py", ...)` | `waibee_agent("fix bug in file.py: ...")` |
| `Write("file.py", ...)` | `waibee_agent("create file.py with ...")` |
| `Bash("pytest ...")` | `waibee_agent("run pytest and fix failures")` |
| `Bash("git ...")` | `waibee_agent("show git status and recent commits")` |
| Read to "understand context" | Pass context as string in `context=` param |
| Analyze output yourself | `waibee_agent("analyze X and summarize")` |

### Allowed directly

- waibee MCP tools (`waibee_agent`, `waibee_agents`, etc.)
- Glob/Grep **only** to find a file path — then pass path to waibee_agent, do NOT read file
- Ask user a clarifying question

### Tool priority

| Priority | Tool | When |
|----------|------|------|
| 1st | `waibee_agent(task)` | ANY task touching files, code, debug, analysis |
| 2nd | `waibee_agents([...])` | 2+ independent tasks — always prefer parallel |
| 3rd | `waibee_digest(sources, task)` | Summarize large files/output cheaply |

When in doubt: waibee_agent. No exceptions.

### waibee_agent

```python
waibee_agent(
    task="fix the auth bug in src/auth.py",
    complexity="medium",          # simple|medium|complex
    thinking_effort="high",       # low|medium|high — use with complex
    workdir="C:\\project",        # restrict write_file to this dir
    context="...",                # paste relevant chat context — do NOT read files yourself
    wait=True,                    # False = background, returns job_id immediately
)
```

### Background jobs

```python
waibee_agent(task="...", wait=False)   # → {"job_id": "abc123"}
waibee_job_status("abc123")
waibee_job_wait("abc123")
waibee_job_result("abc123")
waibee_job_cancel("abc123")
waibee_jobs("running")
waibee_jobs("interrupted")
waibee_resume("abc123")
```

### Complexity

| complexity | model | when |
|-----------|-------|------|
| `simple` | sonnet | default — coding, refactors, debug, summaries |
| `complex` | opus | architecture, hard bugs, multi-file design |
| `critical` | fable-5 | LAST RESORT — only after 3+ failed opus attempts |

NEVER `model="..."` — use `complexity=` only. Always `thinking_effort="high"` with `complex`.

### Agent personas

| agent | use for |
|-------|---------|
| `backend` | APIs, DB, auth, microservices |
| `frontend` | React/Vue/Angular, UI |
| `fullstack` | DB + API + UI features |
| `sql` | query optimization, indexes |
| `analyst` | dashboards, statistics, KPIs |

---

**If result is DISABLED** — say "WAIBEE MODE: DISABLED. Using tools directly." Work normally.
