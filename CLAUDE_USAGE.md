## waibee_mcp

At session start call `waibee_toggle()`. If DISABLED — work normally.

### Token rules (when ENABLED)

**MANDATORY: Every task goes through waibee. Zero exceptions. No self-execution.**

You are a coordinator. You do NOT read files, write code, analyze text, or run commands yourself.
If you catch yourself about to use Read/Edit/Bash/Write for anything non-trivial — STOP. Delegate to waibee_agent instead.

**Violation examples (forbidden):**
- Reading a file to understand it → `waibee_agent("explain X in file.py")`
- Writing code yourself → `waibee_agent("implement X in file.py")`
- Running a command and reading output → `waibee_agent("run pytest and fix failing tests")`
- Analyzing anything → `waibee_agent("analyze Y and summarize")`

**The only things Claude does directly:**
- Call waibee tools
- Apply a waibee_agent result if the agent returned a diff/patch (rare)
- Glob/Grep to find file paths — then immediately pass to waibee_agent

### Priority order (follow strictly)

| Priority | Tool | When |
|----------|------|------|
| **1st** | `waibee_agent(task)` | ANY task touching files, code, debugging, analysis |
| **2nd** | `waibee_agents([...])` | 2+ independent tasks — always prefer parallel |
| **3rd** | `waibee_digest(sources, task)` | Summarize large files/output cheaply, keep CC context small |

When in doubt: use `waibee_agent`.

### waibee_agent — use for everything

```python
waibee_agent(
    task="fix the auth bug in src/auth.py",
    complexity="medium",          # simple|medium|complex
    thinking_effort="high",       # low|medium|high — with complex/opus for hard problems
    workdir="C:\\project",        # restrict writes to this dir
    context="...",                # inject relevant chat context when needed
    wait=True,                    # False = background job, returns job_id immediately
)
```

Agent tools: `read_file`, `write_file`, `bash_run`, `glob_search`, `grep_search`, `list_dir`.
Agent works autonomously. Claude gets only the final result — no intermediate tokens wasted.
All jobs tracked in SQLite: status, steps, cost, trace, checkpoints.

### Background jobs (long tasks)

```python
# Start background job — returns immediately with job_id
result = waibee_agent(task="refactor entire codebase", complexity="complex", wait=False)
# → {"job_id": "abc123", "status": "running", "hint": "waibee_job_status('abc123')"}

# Monitor progress
waibee_job_status("abc123")     # status + last 8 steps trace
waibee_job_wait("abc123")       # attach and receive live notifications (blocks until done)
waibee_job_result("abc123")     # final result when done
waibee_job_cancel("abc123")     # cancel if needed

# List all jobs
waibee_jobs()                   # all recent
waibee_jobs("running")          # currently running
waibee_jobs("interrupted")      # crashed/hit step limit — resumable

# Resume interrupted job (hit step limit or crashed — full message history preserved in DB)
waibee_resume("abc123")         # continues from last checkpoint
waibee_resume("abc123", extra_steps=60)
```

**Live terminal monitor** (separate terminal window — no MCP blocking):
```bash
python watch.py              # all active jobs, auto-refresh every second
python watch.py abc123       # specific job, exits when done
```

### waibee_agents — always prefer for multiple tasks

```python
waibee_agents([
    {"task": "fix bug in api.py", "complexity": "medium", "agent": "backend"},
    {"task": "optimize slow query", "complexity": "complex", "agent": "sql", "thinking_effort": "high"},
    {"task": "add tests for auth.py", "complexity": "medium"},
    # per-agent timeout/retry — prevent one agent from blocking others:
    {"task": "analyze docker logs", "complexity": "simple", "timeout": 120, "retries": 2},
])
```

Never run tasks sequentially if they can run in parallel. Use `waibee_agents`.
Failed agents return `[ERROR]`/`[TIMEOUT]` — others continue unaffected.

### Complexity selection

| complexity | model | when |
|-----------|-------|------|
| `simple` | haiku | summaries, quick fixes, formatting |
| `medium` | sonnet | coding, refactors, debugging (default) |
| `complex` | opus | architecture, hard bugs, design decisions |

Always add `thinking_effort="high"` with `complex` for anything non-trivial.

**NEVER pass** `model="simple"` / `model="medium"` / `model="complex"` — use `complexity` param only. Wrong param causes 400 error.

### Agent selection

| agent | use for |
|-------|---------|
| `backend` | APIs, DB, auth, microservices |
| `frontend` | React/Vue/Angular, UI |
| `fullstack` | DB + API + UI features |
| `sql` | query optimization, indexes |
| `analyst` | dashboards, statistics, KPIs |

### Stats / debug

```python
waibee_stats()                      # today's cost
waibee_models()                     # available models
waibee_log()                        # last 50 log lines — check agent progress
waibee_log(n=20, filter="agent")    # only agent steps
waibee_log(filter="ERROR")          # only errors
```
