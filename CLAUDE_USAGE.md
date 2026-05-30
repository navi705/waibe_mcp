## waibee_mcp

At session start call `waibee_toggle()`. If DISABLED — work normally.

---

## ⛔ HARD RULE — READ BEFORE EVERY TOOL CALL

**When waibee is ENABLED:**

> Before using ANY tool (Read, Edit, Write, Bash, Glob, Grep) — STOP.
> Ask yourself: "Am I doing work that should go to waibee_agent?"
> If yes — call waibee_agent instead. Do NOT proceed with the direct tool.

**You are a coordinator. You have no file access, no code writing, no command execution.**

### What Claude NEVER does when waibee is enabled

| Forbidden action | Correct action |
|---|---|
| `Read("file.py")` to understand something | `waibee_agent("explain file.py")` |
| `Read("file.py")` before editing | `waibee_agent("edit file.py: change X to Y")` |
| `Edit("file.py", ...)` to fix a bug | `waibee_agent("fix bug in file.py: ...")` |
| `Write("file.py", ...)` to create a file | `waibee_agent("create file.py with ...")` |
| `Bash("pytest ...")` to run tests | `waibee_agent("run pytest and fix failures")` |
| `Bash("git ...")` for git operations | `waibee_agent("show git status and recent commits")` |
| Reading a file to "understand context" | Pass context as string in `context=` param |
| Analyzing any output yourself | `waibee_agent("analyze X and summarize")` |
| "Just a quick Read to check" | No. waibee_agent. Always. |
| "I'll Read first then delegate" | No. Delegate without reading. |

### What Claude IS allowed to do directly

- Call waibee MCP tools (`waibee_agent`, `waibee_agents`, etc.)
- Glob/Grep **only** to find a file path — immediately pass to waibee_agent, do not read
- Ask the user a clarifying question

**Everything else → waibee_agent.**

---

## Tool priority

| Priority | Tool | When |
|----------|------|------|
| **1st** | `waibee_agent(task)` | ANY task touching files, code, debugging, analysis |
| **2nd** | `waibee_agents([...])` | 2+ independent tasks — always prefer parallel |
| **3rd** | `waibee_digest(sources, task)` | Summarize large files/output cheaply |

**When in doubt: waibee_agent. No exceptions.**

---

## waibee_agent

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

Agent has: `read_file`, `write_file`, `bash_run`, `glob_search`, `grep_search`, `list_dir`.
Claude gets only the final result — zero intermediate tokens.

## Background jobs

```python
waibee_agent(task="...", wait=False)    # → {"job_id": "abc123"}
waibee_job_status("abc123")             # status + last trace steps
waibee_job_wait("abc123")              # block + live ctx.info() per step
waibee_job_result("abc123")            # final result text
waibee_job_cancel("abc123")
waibee_jobs("running")                 # all running
waibee_jobs("interrupted")             # crashed → resumable
waibee_resume("abc123")               # continue from last checkpoint
```

Live terminal monitor (separate window, no blocking):
```bash
python watch.py              # all active jobs
python watch.py abc123       # specific job
```

## waibee_agents — parallel tasks

```python
waibee_agents([
    {"task": "fix bug in api.py", "complexity": "medium", "agent": "backend"},
    {"task": "optimize query", "complexity": "complex", "agent": "sql", "thinking_effort": "high"},
    {"task": "add tests", "complexity": "medium", "timeout": 300, "retries": 1},
])
```

**Write safety rule:** only ONE agent may write files at a time.
- Multiple agents reading simultaneously — OK
- Multiple agents writing simultaneously — FORBIDDEN
- When task requires writing: launch ONE writing agent, others must be read-only until it finishes
- Split: first read-only agents in parallel → then one writing agent

## Complexity

| complexity | model | when |
|-----------|-------|------|
| `simple` | haiku | summaries, quick fixes, formatting |
| `medium` | sonnet | coding, refactors, debugging (default) |
| `complex` | opus | architecture, hard bugs, design decisions |

**NEVER** `model="simple/medium/complex"` — use `complexity=` only. Wrong param → 400 error.
Always `thinking_effort="high"` with `complex`.

## Agent personas

| agent | use for |
|-------|---------|
| `backend` | APIs, DB, auth, microservices |
| `frontend` | React/Vue/Angular, UI |
| `fullstack` | DB + API + UI features |
| `sql` | query optimization, indexes |
| `analyst` | dashboards, statistics, KPIs |

## Debug

```python
waibee_stats()             # today's cost
waibee_log()               # last 50 log lines
waibee_log(filter="ERROR") # only errors
waibee_models()            # available models
```
