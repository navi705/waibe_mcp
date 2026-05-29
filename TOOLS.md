# waibee_mcp — Tools Reference

All tools check the global enabled flag first. If disabled, they raise an error.
Enable with `waibee_toggle(True)`.

---

## waibee_think

Main inference tool. Send a coding task to the gateway model.

```
waibee_think(
    task: str,
    complexity: str = "auto",
    model: str = None,
    thinking_effort: str = None,
    agent: str = "default",
    system_prompt: str = None,
)
```

### Parameters

**task** — the prompt/question to send. Be specific. Include all context inline.

**complexity** — selects the model:
| Value | Model | Use when |
|-------|-------|----------|
| `simple` | claude-haiku-4-5 | Quick fixes, short snippets, formatting |
| `medium` | claude-sonnet-4-6 | General coding, refactors, debugging |
| `complex` | claude-opus-4-8 | Architecture, hard bugs, stuck situations |
| `auto` | sonnet (default) | Let it default to medium |

**model** — explicit model override, bypasses complexity. Use for experiments.
Example: `model="anthropic/claude-opus-4-8"` or any model from `waibee_models()`.

**thinking_effort** — enables extended reasoning (more accurate, costs more tokens):
| Value | Budget tokens | Use when |
|-------|--------------|----------|
| `low` | 1 000 | Light reasoning boost |
| `medium` | 5 000 | Non-trivial logic problems |
| `high` | 10 000 | Stuck, architectural decisions, complex bugs |

Best combined with `complexity="complex"` (opus). Example:
```
waibee_think("redesign auth flow", complexity="complex", thinking_effort="high")
```

**agent** — selects system prompt from config.json. Optional — omit if no agent fits the task.
| Value | Focus |
|-------|-------|
| `backend` | Node.js/Python/Go APIs, DB, auth, OWASP, microservices |
| `frontend` | React/Vue/Angular, performance, accessibility |
| `fullstack` | End-to-end features, DB to UI |
| `sql` | Query optimization, indexes, execution plans, warehousing |
| `analyst` | BI dashboards, statistics, KPIs, data storytelling |

When to skip agent: quick fixes, generic questions, mixed-domain tasks — default prompt (no agent) is often cleaner.

**system_prompt** — fully overrides agent prompt. Use for one-off custom behavior.

### Examples

```python
# minimal — no agent, no complexity, just the task
waibee_think("write a function that flattens a nested list")

# simple fix
waibee_think("fix off-by-one in this loop: for i in range(1, n)", complexity="simple")

# general task
waibee_think("write a Redis cache wrapper class in Python")

# code review with backend agent
waibee_think("review this auth function for security issues: ...", agent="backend")

# hard architectural problem
waibee_think("we need to migrate from monolith to microservices, suggest approach",
             complexity="complex", thinking_effort="high", agent="fullstack")

# try specific model
waibee_think("explain this regex: ...", model="anthropic/claude-haiku-4-5")
```

---

## waibee_read

Read files internally and process with model. Claude Code never sees the raw file content — saves input tokens.

```
waibee_read(
    paths: list[str],
    task: str,
    raw: bool = False,
    model: str = None,
    agent: str = "default",
)
```

### Parameters

**paths** — list of absolute file paths. Missing files are noted in output, not errors.

**task** — what to do with the file content. Be specific.

**raw** — controls what gets returned:
- `raw=False` (default): model reads files + processes task, returns only the result. Claude Code gets a small summary.
- `raw=True`: skip model, return full file content directly to Claude Code. Use when you need exact content and trust yourself to read it (costs Claude Code input tokens).

**When to use raw=True:** when the task requires Claude Code to have full context and a summary would lose important details. Example: editing a small config file.

**When to use raw=False:** reading large files, logs, generated output — anything where you want a processed result not raw content.

### Examples

```python
# find all API endpoints in a file
waibee_read(["src/api/routes.py"], "list all route definitions with their HTTP methods")

# analyze multiple files
waibee_read(["src/auth.py", "src/middleware.py"], "find potential security issues")

# get summary of a large file
waibee_read(["logs/app.log"], "summarize the last errors")

# get exact content of small config
waibee_read(["config/db.yaml"], "show me the database config", raw=True)
```

---

## waibee_run

Run a shell command and analyze its output with a model. Claude Code never reads the raw output — saves input tokens.

```
waibee_run(
    command: str,
    task: str,
    model: str = None,
)
```

### Parameters

**command** — shell command to execute. Runs with `shell=True`, 60s timeout.

**task** — what to do with the output. The model receives: task + command + exit code + stdout/stderr.

### Nuances

- Always uses `simple` (haiku) by default — output analysis is usually straightforward
- If command fails (non-zero exit code), stderr is included automatically
- Override with `model=` for complex analysis

### Examples

```python
# which tests fail
waibee_run("python -m pytest tests/ -v", "which tests fail and what is the root cause")

# analyze build errors
waibee_run("npm run build", "summarize the build errors")

# check linting
waibee_run("flake8 src/", "list the most critical issues to fix first")

# analyze git log
waibee_run("git log --oneline -20", "summarize recent changes")
```

---

## waibee_parallel

Run multiple independent subtasks simultaneously. All run in parallel via asyncio.gather.

```
waibee_parallel(
    subtasks: list[dict],
    complexity: str = "simple",
    agent: str = "default",
)
```

### Parameters

**subtasks** — list of dicts. Each dict:
```python
{
    "task": str,          # required — the prompt
    "complexity": str,    # optional — overrides top-level default
    "model": str,         # optional — explicit model override
    "agent": str,         # optional — overrides top-level default
}
```

**complexity** — default for all subtasks that don't specify their own.

**agent** — default agent for all subtasks that don't specify their own.

Per-subtask overrides take priority over top-level defaults.

### When to use

- Analyzing multiple files independently
- Running the same analysis on different modules
- Generating multiple independent code pieces that need different models/agents

### When NOT to use

- Tasks that depend on each other's output (run sequentially with waibee_think instead)
- Only 1-2 tasks (just call waibee_think twice)

### Examples

```python
# all subtasks same model (uses default complexity=simple)
waibee_parallel([
    {"task": "analyze src/auth.py for security issues"},
    {"task": "analyze src/db.py for security issues"},
    {"task": "analyze src/api.py for security issues"},
])

# different models and agents per subtask
waibee_parallel([
    {"task": "review auth.py for bugs", "complexity": "medium", "agent": "backend"},
    {"task": "suggest architecture for auth module", "complexity": "complex", "agent": "fullstack"},
    {"task": "write docstrings for auth.py", "complexity": "simple"},
])

# mix of explicit model and complexity
waibee_parallel([
    {"task": "write unit tests for User model", "complexity": "medium"},
    {"task": "write unit tests for Order model", "complexity": "medium"},
    {"task": "write unit tests for Product model", "model": "anthropic/claude-sonnet-4-6"},
])
```

---

## waibee_models

List all available models from the Waibee gateway.

```
waibee_models()
```

Returns a newline-separated list of model IDs. Use to discover available models for the `model=` override parameter in other tools.

### Example

```python
waibee_models()
# → anthropic/claude-haiku-4-5
#   anthropic/claude-sonnet-4-6
#   anthropic/claude-opus-4-8
#   openai/gpt-4o
#   google/gemini-2.0-flash
#   ... etc
```

---

## waibee_toggle

Show status or enable/disable waibee_mcp globally. Persists between sessions via `~/.claude/waibee_mcp.json`.

```
waibee_toggle(enabled: bool = None)
```

### Usage

```python
waibee_toggle()         # show current status, models, api key preview
waibee_toggle(True)     # enable
waibee_toggle(False)    # disable — all other tools return error until re-enabled
```

### Status output includes

- enabled/disabled state
- API key preview (first 12 chars)
- current model mapping (simple/medium/complex)
- caveman_ultra flag

---

## waibee_stats

Show token usage and cost statistics by day.

```
waibee_stats(date_str: str = None)
```

### Parameters

**date_str** — date in `YYYY-MM-DD` format. No arg = today.

### Output includes

- Total requests, input tokens, output tokens, cost in USD
- Breakdown by model

### Examples

```python
waibee_stats()                  # today
waibee_stats("2026-05-27")     # specific day
```

### Sample output

```
Stats [2026-05-27]
  requests : 12
  input    : 18,432 tokens
  output   : 4,201 tokens
  cost     : $0.003847

  by model:
    anthropic/claude-haiku-4-5: req=8 in=10,000 out=2,500 $0.000620
    anthropic/claude-sonnet-4-6: req=4 in=8,432 out=1,701 $0.003227
```

---

## General nuances

**Disabled state** — if `waibee_toggle(False)` was called, all tools except `waibee_toggle` raise:
```
ValueError: waibee_mcp disabled. Call waibee_toggle(True) to enable.
```

**Caveman ultra** — all model responses are prefixed with a terse caveman-style instruction (config: `caveman_ultra: true`). Responses are compressed, fragment-style. Disable in `config.json` if you need verbose output.

**Cost tracking** — cost is taken from `upstream_inference_cost` in the gateway response. This is the actual provider cost. Waibee adds no markup (BYOK).

**Timeouts** — gateway calls timeout at 120s. Shell commands in `waibee_run` timeout at 60s.

**Token efficiency priority** — use `waibee_read` and `waibee_run` aggressively. Every file you read directly in Claude Code costs input tokens that accumulate across the session. Routing through waibee tools keeps Claude Code context lean.
