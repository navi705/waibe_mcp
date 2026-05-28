## waibee_mcp

At session start call `waibee_toggle()`. If DISABLED — work normally.

### Token rules (when ENABLED)

**All inference goes through waibee. No exceptions for "small" tasks.**

Never generate code, analyze files, or process text yourself. Never read files directly. Never read command output directly. Route everything through waibee:

| Instead of | Use |
|-----------|-----|
| Read(file) | `waibee_read([file], task)` |
| Bash(cmd) then read output | `waibee_run(cmd, task)` |
| Generating any code (even small) | `waibee_think(task, complexity)` |
| Analyzing, explaining, summarizing | `waibee_think(task, complexity)` |
| Multiple independent analyses | `waibee_parallel([{task, complexity, agent}])` |

Your role when ENABLED: coordination + applying results via Edit/Bash. Gateway does the thinking.

**Gateway has NO filesystem access.** Never ask waibee to "find files", "search the project", or "look for X in directory". Gateway only sees what you pass it.

Correct pattern:
1. Use Glob/Grep to find files yourself
2. Pass results to waibee_read/waibee_think for analysis

```python
# WRONG — gateway can't find files
waibee_think("find all files related to auth in C:\\project\\")

# CORRECT — Claude Code finds, waibee analyzes
# 1. Glob("**/*auth*") → ["src/auth.py", "src/middleware/auth.py"]
# 2. waibee_read(["src/auth.py", "src/middleware/auth.py"], "analyze auth flow")
```

Exception: `raw=True` in `waibee_read` when exact file content needed for editing.

### Complexity selection

| complexity | model | when |
|-----------|-------|------|
| `simple` | haiku | snippets, formatting, quick fixes, file summarization |
| `medium` | sonnet | general coding, refactors, debugging (default) |
| `complex` | opus | architecture, hard bugs, stuck situations |

Add `thinking_effort="high"` with opus for architectural decisions or when stuck.

**IMPORTANT:** Use `complexity` param for model selection. Never pass `"simple"`, `"medium"`, `"complex"` to the `model` param — `model` is for full model ID override only (e.g. `model="anthropic/claude-haiku-4-5"`). Passing complexity strings to `model` causes 400 errors.

```python
# CORRECT
waibee_read(["file.py"], "summarize", complexity="simple")
waibee_think("task", complexity="medium")

# WRONG — causes 400 Bad Request
waibee_read(["file.py"], "summarize", model="simple")
```

### Agent selection

Only use agent when it clearly fits. Omit otherwise.

| agent | use for |
|-------|---------|
| `backend` | APIs, DB schema, auth, microservices |
| `frontend` | React/Vue/Angular, UI components |
| `fullstack` | features spanning DB + API + UI |
| `sql` | query optimization, indexes, schema design |
| `analyst` | dashboards, statistics, KPIs |

### Parallel pattern

Use `waibee_parallel` when 3+ independent tasks exist:
```python
waibee_parallel([
    {"task": "...", "complexity": "medium", "agent": "backend"},
    {"task": "...", "complexity": "simple"},
    {"task": "...", "complexity": "medium", "agent": "sql"},
])
```

Subtasks can include `paths` to read files as part of the task:
```python
waibee_parallel([
    {"task": "summarize project purpose", "paths": ["DEVELOPMENT.md"], "complexity": "simple"},
    {"task": "extract SDK and dependency versions", "paths": ["android/app/build.gradle.kts", "android/build.gradle.kts"], "complexity": "simple"},
    {"task": "analyze auth flow", "paths": ["src/auth.py"], "complexity": "medium", "agent": "backend"},
])
```

### Stats / models

```python
waibee_stats()           # check today's cost
waibee_models()          # list available models for override
```
