MCP server for Claude Code. Routes AI inference through Waibee gateway to save tokens.

## How it works

```
Claude Code (coordinator — calls waibee tools only)
    ↓ MCP tool call
waibee_mcp server (Python, runs alongside Claude Code)
    ↓ POST X-Title: Waibee
gateway.waibee.com/api/v1
    ↓
Model provider (Anthropic, etc.)
```

Claude Code delegates all file work, code generation, and analysis to waibee agents. Agents autonomously read files, write changes, run commands, and return only the final result.

## Tools

| Tool | What it does |
|------|-------------|
| `waibee_agent` | **Primary.** Agentic loop: model reads/writes files and runs commands autonomously |
| `waibee_agents` | Run multiple independent agentic loops in parallel (with timeout + retry per agent) |
| `waibee_think` | Single inference call — for pure reasoning with no file work |
| `waibee_read` | Read files + summarize with model — saves Claude input tokens |
| `waibee_run` | Run shell command, analyze output with model |
| `waibee_parallel` | Run multiple single-shot tasks in parallel |
| `waibee_log` | Show recent log lines in chat — check agent progress |
| `waibee_models` | List all available models |
| `waibee_toggle` | Enable/disable globally, show status |
| `waibee_stats` | Show token/cost stats by day |

## Agent tools (available inside waibee_agent loop)

| Tool | What it does |
|------|-------------|
| `read_file` | Read file contents |
| `write_file` | Write/overwrite file (sandboxed to workdir if set) |
| `bash_run` | Run PowerShell command |
| `glob_search` | Find files by pattern |
| `grep_search` | Search with ripgrep |
| `list_dir` | List directory contents |

## Requirements

- Python 3.11+
- Claude Code (desktop or VS Code extension)
- Waibee API key (`sk_...` from Waibee VS Code settings → waibeeRouterApiKey)

## Setup

### 1. Clone

```bash
git clone <repo-url>
cd waibee_mcp
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create .env

```bash
cp .env.example .env
```

Edit `.env` and set your Waibee API key:
```
API_KEY=sk_your_key_here
```

Get the key from Waibee settings in VS Code (Settings → waibeeRouterApiKey).

### 4. Register in Claude Code

```bash
claude mcp add -s user waibee-mcp python "C:\path\to\waibee_mcp\server.py"
```

On Windows with Store Python stub, use the full interpreter path:

```bash
claude mcp add -s user waibee-mcp "C:\Users\<you>\AppData\Local\Microsoft\WindowsApps\python.exe" "C:\path\to\waibee_mcp\server.py"
```

Verify with `claude mcp list` — `waibee-mcp` should show `Connected`.

### 5. Add to global CLAUDE.md

Copy `CLAUDE_USAGE.md` content into `~/.claude/CLAUDE.md`.

### 6. Restart Claude Code

### 7. Enable

```
waibee_toggle(True)
```

## Usage

```python
# Check status
waibee_toggle()

# Agentic — agent reads/writes files autonomously
waibee_agent("fix the bug in src/auth.py", complexity="medium")
waibee_agent("refactor gateway.py", complexity="complex", thinking_effort="high")
waibee_agent("add tests for stats.py", workdir="C:\\project")  # write restricted to workdir

# Parallel agents
waibee_agents([
    {"task": "fix api.py", "complexity": "medium", "agent": "backend"},
    {"task": "optimize query in db.py", "complexity": "complex", "agent": "sql"},
    # optional per-agent: timeout=300, retries=1
    {"task": "analyze logs", "complexity": "simple", "timeout": 120, "retries": 2},
])

## waibee_agents — per-agent options

Each agent dict supports:

| key | default | description |
|-----|---------|-------------|
| `task` | required | Task description |
| `complexity` | `medium` | simple\|medium\|complex |
| `thinking_effort` | `null` | low\|medium\|high |
| `agent` | `default` | backend\|frontend\|fullstack\|sql\|analyst |
| `workdir` | `null` | Restrict write_file to this directory |
| `context` | `null` | Extra context to inject |
| `timeout` | `300` | Seconds before agent is killed |
| `retries` | `1` | Retry attempts on failure (0 = no retry) |
| `max_steps` | `20` | Max agentic loop steps |

On failure: result shows `[ERROR]` or `[TIMEOUT]`, other agents continue unaffected.
Tool results inside agent loop are truncated at 8000 chars to prevent context overflow.

# Single-shot (no file access)
waibee_think("design a caching strategy", complexity="complex")
waibee_read(["src/api.py"], "find bugs", complexity="simple")
waibee_run("pytest", "which tests fail and why")

# Debug
waibee_log()                    # last 50 log lines
waibee_log(n=20, filter="agent")  # only agent steps
waibee_log(filter="ERROR")      # only errors

# Stats
waibee_stats()                  # today
waibee_stats("2026-05-27")      # specific day
waibee_models()                 # list available models
```

## Complexity

| complexity | model | when |
|-----------|-------|------|
| `simple` | haiku | quick fixes, summaries |
| `medium` | sonnet | general coding, refactors (default) |
| `complex` | opus | architecture, hard bugs |

Always use `complexity` param — never `model="simple"` (causes 400 error).

Add `thinking_effort="high"` with `complex` for hard problems.

## Config

Edit `config.json` to change models, agent prompts, caveman mode:

```json
{
  "models": {
    "simple": "anthropic/claude-haiku-4-5",
    "medium": "anthropic/claude-sonnet-4-6",
    "complex": "anthropic/claude-opus-4-8"
  },
  "agents": {
    "default": "...",
    "backend": "...",
    "frontend": "...",
    "fullstack": "...",
    "sql": "...",
    "analyst": "..."
  },
  "caveman_ultra": true
}
```

## Logs

Requests and agent steps logged to `logs/waibee_mcp.log` (rotating, 5MB × 3).

Log format:
```
[agent] step=1 tool=read_file args={'path': 'server.py'}
[agent] step=1 tool=read_file result=import asyncio\nimport...
[agent] done steps=3 cost=$0.011096
```

Use `waibee_log()` in Claude Code chat to read logs without leaving the session.

## New machine setup

```bash
# 1. Clone
git clone <repo>
cd waibee_mcp

# 2. Install
pip install -r requirements.txt

# 3. API key
cp .env.example .env
# edit .env → API_KEY=sk_your_key_here

# 4. Register MCP
claude mcp add -s user waibee-mcp python "C:\path\to\waibee_mcp\server.py"
# verify: claude mcp list → waibee-mcp Connected

# 5. Add Claude instructions
# copy CLAUDE_USAGE.md content to ~/.claude/CLAUDE.md

# 6. Restart Claude Code

# 7. Enable
# waibee_toggle(True)
```
