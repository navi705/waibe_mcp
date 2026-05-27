## waibee_mcp

At session start call `waibee_toggle()`. If DISABLED — work normally.

### Token rules (when ENABLED)

Never read files directly. Never read command output directly. Route through waibee:

| Instead of | Use |
|-----------|-----|
| Read(file) | `waibee_read([file], task)` |
| Bash(cmd) then read output | `waibee_run(cmd, task)` |
| Generating large code blocks | `waibee_think(task, complexity)` |
| Multiple independent analyses | `waibee_parallel([{task, complexity, agent}])` |

Exception: `raw=True` in `waibee_read` when exact file content needed for editing.

### Complexity selection

| complexity | model | when |
|-----------|-------|------|
| `simple` | haiku | snippets, formatting, quick fixes, file summarization |
| `medium` | sonnet | general coding, refactors, debugging (default) |
| `complex` | opus | architecture, hard bugs, stuck situations |

Add `thinking_effort="high"` with opus for architectural decisions or when stuck.

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

### Stats / models

```python
waibee_stats()           # check today's cost
waibee_models()          # list available models for override
```
