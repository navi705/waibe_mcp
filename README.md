MCP server for Claude Code. Routes AI inference through Waibee gateway. Agents autonomously read files, write changes, run commands — Claude Code sees only the final result.

## How it works

```
Claude Code (coordinator — calls waibee tools only)
    ↓ MCP tool call
waibee_mcp server (Python, runs alongside Claude Code)
    ↓ POST to gateway.waibee.com/api/v1
Model provider (Anthropic, etc.)
```

## MCP tools

| Tool | What it does |
|------|-------------|
| `waibee_agent` | **Primary.** Agentic loop: agent reads/writes files and runs commands autonomously |
| `waibee_agents` | Run multiple independent agents in parallel (per-agent timeout + retry) |
| `waibee_digest` | One-shot: read files/run commands → model summarizes → short result |
| `waibee_job_status` | Status + recent trace of a background job |
| `waibee_job_wait` | Attach to background job, receive live step notifications (blocks) |
| `waibee_job_result` | Final result of a completed job |
| `waibee_job_cancel` | Cancel a running background job |
| `waibee_jobs` | List recent jobs, filter by status |
| `waibee_resume` | Resume an interrupted job from last checkpoint |
| `waibee_log` | Show recent log lines in chat |
| `waibee_models` | List available models |
| `waibee_toggle` | Enable/disable globally, show status |
| `waibee_stats` | Token/cost stats by day |

## Agent tools (inside waibee_agent loop)

| Tool | What it does |
|------|-------------|
| `read_file` | Read file contents |
| `write_file` | Write/overwrite file (sandboxed to workdir if set) |
| `bash_run` | Run PowerShell command (blocklisted dangerous commands, 30s timeout) |
| `glob_search` | Find files by pattern |
| `grep_search` | Search with ripgrep |
| `list_dir` | List directory contents |

## Requirements

- Python 3.11+
- Claude Code (desktop or VS Code extension)
- Waibee API key (`sk_...` from Waibee VS Code settings → waibeeRouterApiKey)

## Setup

### 1. Clone and install

```bash
git clone <repo-url>
cd waibee_mcp
pip install -r requirements.txt
```

### 2. API key

```bash
cp .env.example .env
# edit .env → API_KEY=sk_your_key_here
```

### 3. Register in Claude Code

```bash
claude mcp add -s user waibee-mcp python "C:\path\to\waibee_mcp\server.py"
```

> **Windows MS Store Python:** if `python` resolves to the Store stub, use the full path:
> ```bash
> claude mcp add -s user waibee-mcp "C:\Python311\python.exe" "C:\path\to\waibee_mcp\server.py"
> ```

Verify: `claude mcp list` → `waibee-mcp Connected`

### 4. Add to CLAUDE.md

Copy `CLAUDE_USAGE.md` into `~/.claude/CLAUDE.md`.

### 5. Restart Claude Code and enable

```
waibee_toggle(True)
```

## Usage

```python
# Single agent (blocks until done)
waibee_agent("fix the bug in src/auth.py", complexity="medium")
waibee_agent("refactor gateway.py", complexity="complex", thinking_effort="high")
waibee_agent("add tests", workdir="C:\\project")   # write_file restricted to workdir

# Background job — returns job_id immediately
waibee_agent("refactor entire codebase", complexity="complex", wait=False)
# → {"job_id": "abc123", "status": "running"}

# Check / wait
waibee_job_status("abc123")    # status + last 8 trace steps
waibee_job_wait("abc123")      # block + live notifications per step
waibee_job_result("abc123")    # final result text
waibee_jobs("running")         # all running jobs
waibee_jobs("interrupted")     # crashed or hit step limit — resumable
waibee_resume("abc123")        # continue from last checkpoint

# Parallel agents
waibee_agents([
    {"task": "fix api.py", "complexity": "medium", "agent": "backend"},
    {"task": "optimize query", "complexity": "complex", "agent": "sql", "thinking_effort": "high"},
    {"task": "analyze logs", "complexity": "simple", "timeout": 120, "retries": 2},
])

# One-shot digest (no agentic loop — just read + summarize)
waibee_digest(["src/api.py", "cmd:git log --oneline -20"], "summarize recent changes")

# Debug
waibee_log()                      # last 50 log lines
waibee_log(filter="ERROR")        # only errors
waibee_stats()                    # today's cost
```

## Live monitor (separate terminal)

Watch background jobs without blocking Claude Code:

```bash
python watch.py              # all active jobs, refreshes every second
python watch.py abc123       # specific job — exits when done
```

Shows: job status, elapsed time, step count, cost, and last 8 tool calls with args/results.

## waibee_agent parameters

| param | default | description |
|-------|---------|-------------|
| `task` | required | Task description |
| `complexity` | `medium` | `simple`\|`medium`\|`complex` → haiku\|sonnet\|opus |
| `thinking_effort` | `null` | `low`\|`medium`\|`high` — use with `complex` |
| `agent` | `default` | `backend`\|`frontend`\|`fullstack`\|`sql`\|`analyst` |
| `workdir` | `null` | Restrict `write_file` to this directory |
| `context` | `null` | Extra context to inject into agent prompt |
| `max_steps` | `100` | Step limit (wall_clock_s is the primary guard) |
| `wall_clock_s` | `900` | Hard time cap in seconds (15 min) |
| `wait` | `True` | `False` = return job_id immediately |
| `allow_dangerous` | `False` | Bypass bash_run blocklist |

## waibee_agents per-agent options

| key | default | description |
|-----|---------|-------------|
| `task` | required | Task description |
| `complexity` | `medium` | simple\|medium\|complex |
| `thinking_effort` | `null` | low\|medium\|high |
| `agent` | `default` | Agent persona |
| `workdir` | `null` | Restrict write_file |
| `context` | `null` | Extra context |
| `timeout` | `300` | Seconds before agent killed |
| `retries` | `1` | Retry attempts on failure |
| `max_steps` | `100` | Per-agent step limit |

## Complexity

| complexity | model | when |
|-----------|-------|------|
| `simple` | haiku | summaries, quick fixes, formatting |
| `medium` | sonnet | coding, refactors, debugging (default) |
| `complex` | opus | architecture, hard bugs, design decisions |

Always use `complexity` param — never `model="simple"` (causes 400 error).  
Add `thinking_effort="high"` with `complex` for hard problems.

## Reliability features

- **Hang prevention:** bash_run uses async subprocess + 30s timeout + process tree kill
- **Loop detection:** agent warned automatically if same tool+args called 3× in a row
- **Crash recovery:** every message written to SQLite immediately — `waibee_resume` restores full context
- **Orphan detection:** on startup, stale running jobs (>240s no heartbeat) marked as interrupted
- **Wall-clock cap:** 900s hard limit per agent regardless of step count

## Config

Edit `config.json`:

```json
{
  "models": {
    "simple": "anthropic/claude-haiku-4-5",
    "medium": "anthropic/claude-sonnet-4-6",
    "complex": "anthropic/claude-opus-4-8"
  },
  "agents": {
    "default": "Senior software engineer...",
    "backend": "...",
    "frontend": "...",
    "fullstack": "...",
    "sql": "...",
    "analyst": "..."
  },
  "caveman_ultra": true
}
```

Bash safety rules (timeout, error recovery, loop discipline) are injected into every agent prompt automatically — no need to repeat them in custom agent prompts.

## Logs

Requests and agent steps logged to `logs/waibee_mcp.log` (rotating, 5MB × 3).

```
[agent] step=1 tool=read_file args={'path': 'server.py'}
[agent] step=1 tool=read_file result=import asyncio...
[agent] done steps=7 cost=$0.021
```
