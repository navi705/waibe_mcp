MCP server for Claude Code. Routes AI inference through Waibee gateway to save tokens.

## How it works

```
Claude Code (coordination + execution via Edit/Bash/Read)
    ↓ MCP tool call
waibee_mcp server (Python, runs alongside Claude Code)
    ↓ POST X-Title: Waibee
gateway.waibee.com/api/v1
    ↓
Model provider (Anthropic, etc.)
```

Claude Code keeps its native tools. Heavy inference (code generation, analysis, file summarization) goes through the gateway using cheaper models. Token savings: 4-8x on generation-heavy tasks.

## Tools

| Tool | What it does |
|------|-------------|
| `waibee_think` | Send coding task to gateway model |
| `waibee_read` | Read files internally, return summary — saves Claude Code input tokens |
| `waibee_run` | Run shell command, analyze output with model |
| `waibee_parallel` | Run multiple subtasks in parallel |
| `waibee_models` | List all available models |
| `waibee_toggle` | Enable/disable globally, show status |
| `waibee_stats` | Show token/cost stats by day |

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

Use the `claude mcp add` command — this is the correct way. Do **not** edit `settings.json` manually (Claude Code ignores `mcpServers` there).

```bash
claude mcp add -s user waibee-mcp python "C:\path\to\waibee_mcp\server.py"
```

Replace `C:\path\to\waibee_mcp` with the actual absolute path where you cloned the repo.

On Windows with the Store Python stub, use the full path to the real interpreter:

```bash
claude mcp add -s user waibee-mcp "C:\Users\<you>\AppData\Local\Microsoft\WindowsApps\python.exe" "C:\path\to\waibee_mcp\server.py"
```

The `-s user` flag registers the server in `~/.claude.json` (user-level, available in all projects). Omit it to register only for the current project. After running, verify with:

```bash
claude mcp list
```

`waibee-mcp` should appear as `Connected`.

### 5. Add to global CLAUDE.md

Copy the full content of `CLAUDE_USAGE.md` into `~/.claude/CLAUDE.md`.
This tells Claude Code when and how to use waibee tools automatically.

### 6. Restart Claude Code

Server starts automatically with Claude Code.

### 7. Enable

In Claude Code session:
```
waibee_toggle(True)
```

## Usage

```
waibee_toggle()                          # check status
waibee_think("write a function that flattens a nested list")  # minimal, no params needed
waibee_think("write X", complexity="medium")
waibee_read(["src/api.py"], "find bugs")                      # complexity="simple" default
waibee_read(["src/api.py"], "find bugs", complexity="medium") # use complexity, NOT model param
waibee_run("pytest", "which tests fail and why")
waibee_parallel([                                # each subtask can have own model/agent
    {"task": "task1"},
    {"task": "task2", "complexity": "medium", "agent": "backend"},
    {"task": "task3", "complexity": "complex", "agent": "sql"},
])
waibee_models()
waibee_stats()                           # today
waibee_stats("2026-05-27")              # specific day
```

## Config

Edit `config.json` to change models, add agent prompts, toggle caveman mode.

```json
{
  "models": {
    "simple": "anthropic/claude-haiku-4-5",
    "medium": "anthropic/claude-sonnet-4-6",
    "complex": "anthropic/claude-opus-4-7"
  },
  "agents": {
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

Requests and errors logged to `logs/waibee_mcp.log` (rotating, max 5MB × 3 files). Check first when debugging gateway errors.

## New machine setup

```bash
# 1. Clone
git clone <repo>
cd waibee_mcp

# 2. Install
pip install -r requirements.txt

# 3. API key
cp .env.example .env
# edit .env → set API_KEY=sk_your_key_here

# 4. Register MCP server
claude mcp add -s user waibee-mcp python "C:\path\to\waibee_mcp\server.py"
# verify: claude mcp list → waibee-mcp should show Connected

# 5. Add Claude Code instruction
# add waibee_mcp section to ~/.claude/CLAUDE.md (see step 5 above)

# 6. Restart Claude Code

# 7. Enable
# run in Claude Code: waibee_toggle(True)
```
