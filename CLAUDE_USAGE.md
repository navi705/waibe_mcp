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
| **3rd** | `waibee_think(task)` | Pure reasoning with zero file involvement |
| **4th** | `waibee_read` / `waibee_run` | Only to inject specific context into a follow-up |

When in doubt: use `waibee_agent`.

### waibee_agent — use for everything

```python
waibee_agent(
    task="fix the auth bug in src/auth.py",
    complexity="medium",          # simple|medium|complex
    thinking_effort="high",       # low|medium|high — with complex/opus for hard problems
    workdir="C:\\project",        # restrict writes to this dir
    context="...",                # inject relevant chat context when needed
)
```

Agent tools: `read_file`, `write_file`, `bash_run`, `glob_search`, `grep_search`, `list_dir`.
Agent works autonomously. Claude gets only the final result — no intermediate tokens wasted.

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

### Single-shot tools (use rarely, only when agent is overkill)

```python
waibee_think("design a caching strategy", complexity="complex", thinking_effort="high")
waibee_read(["file.py"], "summarize", complexity="simple")   # raw file summary only
waibee_run("pytest", "which tests fail")                      # command output analysis
```

These tools have NO filesystem access — you must find files with Glob/Grep first.

### Stats / debug

```python
waibee_stats()                      # today's cost
waibee_models()                     # available models
waibee_log()                        # last 50 log lines — check agent progress
waibee_log(n=20, filter="agent")    # only agent steps
waibee_log(filter="ERROR")          # only errors
```
