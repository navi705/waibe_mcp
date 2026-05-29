import asyncio
import glob as glob_module
import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path

import fastmcp
import logging

from config import get_config, get_api_key, is_enabled, set_flag
import gateway
import stats as stats_module
from prompts import build_system_prompt

logger = logging.getLogger("waibee_mcp")

mcp = fastmcp.FastMCP("waibee")


def _check_enabled():
    if not is_enabled():
        raise ValueError("waibee_mcp disabled. Call waibee_toggle(True) to enable.")


def _resolve_model(complexity: str, model_override: str = None) -> str:
    if model_override:
        return model_override
    cfg = get_config()
    c = complexity if complexity != "auto" else "medium"
    return cfg["models"].get(c, cfg["models"]["medium"])


def _get_system_prompt(agent: str, system_override: str = None) -> str:
    cfg = get_config()
    caveman = cfg.get("caveman_ultra", True)
    agent_prompt = cfg["agents"].get(agent, cfg["agents"]["default"])
    return build_system_prompt(agent_prompt, system_override, caveman)


@mcp.tool()
async def waibee_think(
    task: str,
    complexity: str = "auto",
    model: str = None,
    thinking_effort: str = None,
    agent: str = "default",
    system_prompt: str = None,
) -> str:
    """
    Send coding task to Waibee gateway model.
    complexity: simple|medium|complex|auto
    thinking_effort: low|medium|high (enables extended reasoning)
    agent: default|reviewer|architect
    """
    _check_enabled()
    resolved = _resolve_model(complexity, model)
    sys_prompt = _get_system_prompt(agent, system_prompt)
    messages = [{"role": "user", "content": task}]
    effort = thinking_effort or get_config().get("default_thinking_effort")
    return await gateway.call(messages, resolved, sys_prompt, effort)


@mcp.tool()
async def waibee_read(
    paths: list[str],
    task: str,
    raw: bool = False,
    complexity: str = "simple",
    model: str = None,
    agent: str = "default",
) -> str:
    """
    Read files and process with model. Saves Claude Code input tokens.
    complexity: simple|medium|complex
    raw=True: return file content directly without model processing.
    raw=False: model summarizes/processes files, returns only result.
    """
    _check_enabled()

    contents = []
    for path in paths:
        p = Path(path)
        if not p.exists():
            contents.append(f"[NOT FOUND: {path}]")
            continue
        contents.append(f"=== {path} ===\n{p.read_text(encoding='utf-8', errors='replace')}")

    combined = "\n\n".join(contents)

    if raw:
        return combined

    resolved = _resolve_model(complexity, model)
    sys_prompt = _get_system_prompt(agent)
    messages = [{"role": "user", "content": f"{task}\n\n{combined}"}]
    return await gateway.call(messages, resolved, sys_prompt)


@mcp.tool()
async def waibee_run(
    command: str,
    task: str,
    model: str = None,
) -> str:
    """
    Run shell command and analyze output with model.
    Saves Claude Code input tokens from reading large command output.
    """
    _check_enabled()

    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=60
    )
    output = result.stdout
    if result.stderr:
        output += f"\nSTDERR:\n{result.stderr}"

    resolved = _resolve_model("simple", model)
    cfg = get_config()
    sys_prompt = build_system_prompt(
        cfg["agents"]["default"], caveman=cfg.get("caveman_ultra", True)
    )
    messages = [
        {
            "role": "user",
            "content": (
                f"{task}\n\nCommand: {command}\n"
                f"Exit code: {result.returncode}\nOutput:\n{output}"
            ),
        }
    ]
    return await gateway.call(messages, resolved, sys_prompt)


@mcp.tool()
async def waibee_parallel(
    subtasks: list[dict],
    complexity: str = "simple",
    agent: str = "default",
) -> str:
    """
    Run multiple subtasks in parallel via gateway. Returns combined results.
    Each subtask is a dict: {"task": str, "complexity"?: str, "model"?: str, "agent"?: str}
    Top-level complexity/agent are defaults, overridden per subtask.
    """
    _check_enabled()

    async def run_one(i: int, item: dict) -> str:
        task = item["task"]
        if "paths" in item:
            contents = []
            for path in item["paths"]:
                p = Path(path)
                if not p.exists():
                    contents.append(f"[NOT FOUND: {path}]")
                else:
                    contents.append(f"=== {path} ===\n{p.read_text(encoding='utf-8', errors='replace')}")
            if contents:
                task = task + "\n\n" + "\n\n".join(contents)
        resolved = _resolve_model(item.get("complexity", complexity), item.get("model"))
        sys_prompt = _get_system_prompt(item.get("agent", agent))
        messages = [{"role": "user", "content": task}]
        result = await gateway.call(messages, resolved, sys_prompt)
        return f"=== Subtask {i + 1} ===\n{result}"

    results = await asyncio.gather(*[run_one(i, t) for i, t in enumerate(subtasks)])
    return "\n\n".join(results)


AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Absolute or relative file path"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to file (creates or overwrites)",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash_run",
            "description": "Run a PowerShell command and return output",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_search",
            "description": "Find files matching a glob pattern",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern e.g. src/**/*.py"},
                    "root": {"type": "string", "description": "Root directory (optional)"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "Search for a regex pattern in files",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "File or directory to search"},
                    "file_glob": {"type": "string", "description": "Limit to files matching glob e.g. *.py"},
                },
                "required": ["pattern", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and directories in a path",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
]

# Dangerous command patterns for bash_run blocklist (case-insensitive, matched anywhere in command).
# Bypass with allow_dangerous=True in waibee_agent / waibee_agents.
BASH_BLOCKLIST = [
    # ── Filesystem destruction ────────────────────────────────────────────────
    r"rm\s+-rf",                                                            # rm -rf
    r"Remove-Item\s+.*-Recurse.*-Force|Remove-Item\s+.*-Force.*-Recurse",  # PS Remove-Item -Recurse -Force
    r"Remove-Item\b.*\\?(C:\\?|\$env:SystemRoot|\$env:windir|%windir%|%systemroot%)",  # Wipes Windows dir
    r"del\s+/f\s+/s",                                                       # Windows del /f /s
    r"rd\s+/s\s+/q",                                                        # Windows rd /s /q
    r"\bcipher\s+/w",                                                       # Secure overwrite free space
    r"\bsdelete(64)?\b.*-[psz]",                                            # Sysinternals secure delete
    r"fsutil\s+file\s+setzerodata",                                         # Zeroes file contents
    r"fsutil\s+volume\s+(dismount|allocationreport)",                       # Dismounts live volume
    r"dd\s+.*of=/dev/(sd[a-z]|nvme|disk)",                                  # Raw disk overwrite
    r"\b(takeown|icacls)\b.*(\\Windows|\\System32|\\Program Files)",        # Hijacks system ACLs
    r">\s*\$PROFILE|Set-Content\s+.*\$PROFILE",                             # Overwrites PS profile
    r"\bdiskpart\b",                                                        # diskpart
    r"\bformat\b.*[a-z]:[/\\]",                                             # format drive

    # ── Git history destruction ───────────────────────────────────────────────
    r"git\s+push",                                                          # git push (any variant)
    r"git\b.*--force",                                                      # git.*--force
    r"git\s+reset\s+--hard\b",                                              # git reset --hard
    r"git\s+clean\s+-[fdx]{2,}",                                            # git clean -fdx
    r"git\s+checkout\s+\.\s*$",                                             # git checkout .
    r"git\s+rebase\s+.*(-i\s+--root|--force)",                              # Rewrites history
    r"git\s+filter-(branch|repo)\b",                                        # Rewrites history globally
    r"git\s+update-ref\s+-d\b",                                             # Deletes branch ref
    r"git\s+reflog\s+(delete|expire)\b",                                    # Destroys recovery log
    r"git\s+gc\s+.*--prune=(now|all)",                                      # Drops unreachable objects
    r"git\s+branch\s+-D\b",                                                 # Force-deletes branch
    r"git\s+push\s+.*--delete\b",                                           # Deletes remote branch/tag
    r"git\s+remote\s+(remove|rm|set-url)\b",                                # Hijacks remote
    r"git\s+config\s+.*(core\.hooksPath|alias\.)",                          # Plants malicious hooks
    r"git\s+submodule\s+deinit\s+--force",                                  # Removes submodule trees

    # ── System damage ─────────────────────────────────────────────────────────
    r"(reg\s+(add|delete|import)|Set-ItemProperty|New-ItemProperty|Remove-ItemProperty)\b.*HK(LM|CU|CR|U)",  # Registry mutation
    r"(Stop-Service|sc(\.exe)?\s+(stop|delete|config)|net\s+stop)\b.*(Defender|WinDefend|MpsSvc|wuauserv|BITS|EventLog|LanmanServer)",  # Kills critical service
    r"Set-MpPreference\b.*-Disable",                                        # Disables Defender
    r"Add-MpPreference\b.*-ExclusionPath",                                  # Whitelists malware path
    r"\bbcdedit\b",                                                         # Boot config modification
    r"DISM\b.*(RestoreHealth|Remove-Package|Disable-Feature)",              # System feature mutation
    r"\bpnputil\b.*[-/]i",                                                  # Driver install
    r"\bvssadmin\s+delete\s+shadows",                                       # Destroys shadow copies (ransomware TTP)
    r"\bwbadmin\s+delete",                                                  # Destroys backup catalog
    r"\bmanage-bde\b.*-(off|lock)",                                         # BitLocker manipulation
    r"\bshutdown\b",                                                        # shutdown
    r"restart-computer",                                                    # PS Restart-Computer

    # ── Package managers (system-wide risk) ───────────────────────────────────
    r"npm\s+(install|i)\s+(-g|--global)\b",                                 # Global npm install
    r"npm\s+(audit\s+fix\s+--force|update\s+--force)",                      # Force dep bump
    r"\b(choco|scoop|winget)\s+(install|upgrade|uninstall)\b",              # System package mutation
    r"(Iex|Invoke-Expression)\b.*(Invoke-WebRequest|Invoke-RestMethod|iwr|irm|curl|wget)",  # Remote script exec
    r"(iwr|irm)\b.*\|\s*iex",                                               # Pipe-to-iex
    r"curl\s+.*\|\s*(sh|bash|powershell|pwsh)",                             # Pipe-to-shell
    r"Install-Module\b.*-Scope\s+AllUsers",                                 # System-wide PS module
    r"Set-PSRepository\b.*-InstallationPolicy\s+Trusted",                   # Lowers module trust

    # ── Network / firewall / exfiltration ─────────────────────────────────────
    r"netsh\s+advfirewall\s+set\s+.*state\s+off",                           # Disables firewall
    r"netsh\s+advfirewall\s+firewall\s+add\s+rule",                         # Adds firewall rule
    r"Set-NetFirewallProfile\b.*-Enabled\s+False",                          # Disables firewall
    r"\bNew-NetFirewallRule\b",                                             # Adds firewall rule
    r"Add-Content\s+.*\\drivers\\etc\\hosts",                               # hosts file poisoning
    r"(Invoke-WebRequest|Invoke-RestMethod|curl|wget)\b.*-(Method\s+)?(Post|Put)\b.*(\$env:|\.env|id_rsa|\.aws|\.ssh)",  # Exfil secrets

    # ── Credential theft ──────────────────────────────────────────────────────
    r"(Get-Content|cat|type|gc)\s+.*\.(env|pem|key|ppk)(\b|['\"])",        # Reads secret file
    r"(Get-Content|cat|gc|type)\s+.*[/\\]\.ssh[/\\](id_rsa|id_ed25519|id_ecdsa)\b",  # SSH private key
    r"(Get-Content|cat|gc)\s+.*[/\\]\.aws[/\\]credentials",                # AWS creds
    r"\b(cmdkey|vaultcmd)\b\s+/list",                                       # Enumerates credentials
    r"ConvertFrom-SecureString\b",                                          # Extracts plaintext cred
    r"\b(mimikatz|procdump)\b.*lsass",                                      # LSASS dump
    r"reg\s+save\s+HK(LM|U)\\(SAM|SYSTEM|SECURITY)",                       # Hive dump

    # ── Process killing ───────────────────────────────────────────────────────
    r"Stop-Process\b.*-Name\s+\*",                                          # Kills all processes
    r"(Stop-Process|taskkill)\b.*(lsass|csrss|wininit|services|smss|winlogon|MsMpEng)\b",  # Kills system process
    r"taskkill\b.*/F\s+/IM\s+\*",                                           # Mass process termination

    # ── Persistence ───────────────────────────────────────────────────────────
    r"\bschtasks\b.*/create",                                               # Scheduled task persistence
    r"Register-ScheduledTask\b|New-ScheduledTask\b",                        # Scheduled task persistence
    r"reg\s+add\b.*(CurrentVersion\\Run|RunOnce|Winlogon|Image File Execution Options)",  # Autorun persistence
    r"\bNew-Service\b|sc(\.exe)?\s+create\b",                               # Service persistence

    # ── PowerShell bypass ─────────────────────────────────────────────────────
    r"Set-ExecutionPolicy\b.*(Bypass|Unrestricted)\b",                      # Lowers exec policy
    r"powershell(\.exe)?\b.*(ExecutionPolicy\s+Bypass|-ep\s+bypass)",       # Bypass exec policy
    r"powershell(\.exe)?\b.*-(EncodedCommand|enc)\b",                       # Obfuscated command
    r"\[Reflection\.Assembly\]::Load\b|Add-Type\b.*-TypeDefinition",        # In-memory .NET load
    r"\bInvoke-Expression\b|\biex\b\s+\$",                                  # Dynamic code execution

    # ── Interactive / long-running blockers ───────────────────────────────────
    r"^\s*python(\d(\.\d+)?)?\s*$",                                         # Bare python → MS Store/REPL
    r"^\s*(node|irb|pry|ipython|bash|sh|pwsh|powershell|cmd)\s*$",          # Interactive REPL
    r"\btail\s+-f\b|Get-Content\b.*-Wait\b",                                # Follows file forever
    r"^\s*watch\b",                                                         # Loops forever
    r"\bping\b(?!.*-n\s+\d+)(?!.*-c\s+\d+)",                               # Infinite ping (Windows default)
    r"Start-Sleep\b.*-Seconds\s+(\d{4,})",                                  # Very long sleep
    r"\bRead-Host\b|\bpause\b\s*$|cmd\b.*/k\b",                             # Waits for stdin
    r"\b(npm|yarn|pnpm)\s+(start|run\s+(dev|serve|watch))\b",               # Dev server (long-running)
    r"\b(flask|django-admin)\s+run\b|\buvicorn\b|\bgunicorn\b",             # Python server
    r"\bdocker\s+(run(?!.*--rm.*-d)|exec\s+-it|attach)\b",                  # Interactive container
    r"\bssh\b(?!.*\b(-o\s+BatchMode=yes|-T)\b)",                            # Interactive SSH
    r"\btcpdump\b|\bwireshark\b",                                           # Sniffer

    # ── SQL destruction ───────────────────────────────────────────────────────
    r"DROP\s+TABLE",                                                        # SQL DROP TABLE
    r"DROP\s+DATABASE",                                                     # SQL DROP DATABASE
]

# Pre-compiled for performance
_BASH_BLOCKLIST_RE = [re.compile(p, re.IGNORECASE) for p in BASH_BLOCKLIST]


def _check_bash_blocklist(command: str) -> str | None:
    """Return matched pattern string if command is blocked, else None."""
    for pattern, compiled in zip(BASH_BLOCKLIST, _BASH_BLOCKLIST_RE):
        if compiled.search(command):
            return pattern
    return None


def _exec_tool(name: str, args: dict, workdir: str = None, allow_dangerous: bool = False) -> str:
    try:
        if name == "read_file":
            return Path(args["path"]).read_text(encoding="utf-8", errors="replace")

        elif name == "write_file":
            p = Path(args["path"])
            if workdir:
                wd = Path(workdir).resolve()
                try:
                    p.resolve().relative_to(wd)
                except ValueError:
                    return f"[DENIED] write_file outside workdir: {p}"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"], encoding="utf-8")
            return f"Written {len(args['content'])} chars to {p}"

        elif name == "bash_run":
            if not allow_dangerous:
                matched = _check_bash_blocklist(args["command"])
                if matched is not None:
                    return (
                        f"[BLOCKED] dangerous command: {matched}. "
                        "Use workdir restrictions or request user confirmation."
                    )
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", args["command"]],
                    capture_output=True, text=True, timeout=30,
                    cwd=workdir,
                )
                out = result.stdout
                if result.stderr:
                    out += f"\nSTDERR:\n{result.stderr}"
                return out or f"(exit {result.returncode})"
            except subprocess.TimeoutExpired:
                return "[TIMEOUT] command exceeded 30s — killed. Avoid long-running or interactive commands."

        elif name == "glob_search":
            root = args.get("root", workdir or ".")
            matches = glob_module.glob(args["pattern"], root_dir=root, recursive=True)
            return "\n".join(matches) if matches else "(no matches)"

        elif name == "grep_search":
            cmd = ["rg", "--line-number", args["pattern"], args["path"]]
            if args.get("file_glob"):
                cmd += ["--glob", args["file_glob"]]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.stdout or "(no matches)"

        elif name == "list_dir":
            entries = os.listdir(args["path"])
            return "\n".join(entries)

        else:
            return f"[UNKNOWN TOOL: {name}]"

    except Exception as e:
        return f"[ERROR] {name}: {e}"


async def _run_agent_loop(
    task: str,
    model: str,
    sys_prompt: str,
    thinking_effort: str = None,
    max_steps: int = 40,
    workdir: str = None,
    context: str = None,
    allow_dangerous: bool = False,
) -> str:
    content = task
    if context:
        content = f"Context:\n{context}\n\nTask:\n{task}"

    messages = [{"role": "user", "content": content}]
    total_cost = 0.0
    step = 0

    while step < max_steps:
        step += 1
        resp = await gateway.call_with_tools(
            messages=messages,
            model=model,
            tools=AGENT_TOOLS,
            system_prompt=sys_prompt,
            thinking_effort=thinking_effort,
        )
        total_cost += resp["cost"]

        assistant_msg = {"role": "assistant"}
        if resp["content"]:
            assistant_msg["content"] = resp["content"]
        if resp["tool_calls"]:
            assistant_msg["tool_calls"] = resp["tool_calls"]
        messages.append(assistant_msg)

        if resp["finish_reason"] != "tool_calls" or not resp["tool_calls"]:
            break

        tool_results = []
        for tc in resp["tool_calls"]:
            fn = tc["function"]
            name = fn["name"]
            try:
                args = json.loads(fn["arguments"])
            except Exception:
                args = {}
            args_preview = str(args)[:120]
            logger.info(f"[agent] step={step} tool={name} args={args_preview}")
            result = await asyncio.to_thread(_exec_tool, name, args, workdir, allow_dangerous)
            result_preview = result[:80].replace("\n", "\\n")
            logger.info(f"[agent] step={step} tool={name} result={result_preview}")
            MAX_TOOL_RESULT = 8000
            if len(result) > MAX_TOOL_RESULT:
                result = result[:MAX_TOOL_RESULT] + f"\n...[truncated, total {len(result)} chars]"
            tool_results.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

        messages.extend(tool_results)

    final = resp.get("content")
    if not final:
        # hit max_steps mid-loop — ask model to summarize what was done
        logger.warning(f"[agent] max_steps={max_steps} reached, requesting summary")
        messages.append({"role": "user", "content": "You have reached the step limit. Summarize what you have done so far and what remains."})
        summary_resp = await gateway.call_with_tools(
            messages=messages, model=model, tools=AGENT_TOOLS,
            system_prompt=sys_prompt, thinking_effort=thinking_effort,
        )
        total_cost += summary_resp["cost"]
        final = summary_resp.get("content") or "(no response — max steps reached)"
    logger.info(f"[agent] done steps={step} cost=${total_cost:.6f}")
    return f"{final}\n\n[steps: {step}, cost: ${total_cost:.6f}]"


@mcp.tool()
async def waibee_agent(
    task: str,
    complexity: str = "medium",
    model: str = None,
    thinking_effort: str = None,
    agent: str = "default",
    max_steps: int = 40,
    workdir: str = None,
    context: str = None,
    system_prompt: str = None,
    allow_dangerous: bool = False,
) -> str:
    """
    Agentic loop: model autonomously reads/writes files and runs commands until task is done.
    complexity: simple|medium|complex
    thinking_effort: low|medium|high (use with complex/opus for hard problems)
    workdir: restrict write_file to this directory
    context: optional chat context to inject
    allow_dangerous: set True to bypass bash_run blocklist (default False). Blocklist blocks
        destructive commands: git push, rm -rf, Remove-Item -Recurse -Force, format, diskpart,
        DROP TABLE/DATABASE, del /f /s, rd /s /q, shutdown, restart-computer, and any git
        command with --force.
    """
    _check_enabled()
    resolved = _resolve_model(complexity, model)
    sys_prompt = _get_system_prompt(agent, system_prompt)
    return await _run_agent_loop(
        task, resolved, sys_prompt, thinking_effort, max_steps, workdir, context, allow_dangerous
    )


@mcp.tool()
async def waibee_agents(
    agents: list[dict],
    max_steps: int = 40,
    allow_dangerous: bool = False,
) -> str:
    """
    Run multiple independent agentic loops in parallel.
    Each agent dict: {task, complexity?, model?, thinking_effort?, agent?, workdir?, context?, system_prompt?, allow_dangerous?}
    allow_dangerous: global default; per-agent allow_dangerous overrides this.
    """
    _check_enabled()

    async def run_one(i: int, item: dict) -> str:
        resolved = _resolve_model(item.get("complexity", "medium"), item.get("model"))
        sys_prompt = _get_system_prompt(item.get("agent", "default"), item.get("system_prompt"))
        label = item.get("task", "")[:60]
        agent_name = item.get("agent", "default")
        timeout = item.get("timeout", 300)
        retries = item.get("retries", 1)
        item_allow_dangerous = item.get("allow_dangerous", allow_dangerous)

        last_error = None
        for attempt in range(1, retries + 2):
            try:
                result = await asyncio.wait_for(
                    _run_agent_loop(
                        task=item["task"],
                        model=resolved,
                        sys_prompt=sys_prompt,
                        thinking_effort=item.get("thinking_effort"),
                        max_steps=item.get("max_steps", max_steps),
                        workdir=item.get("workdir"),
                        context=item.get("context"),
                        allow_dangerous=item_allow_dangerous,
                    ),
                    timeout=timeout,
                )
                attempt_suffix = f" [attempt {attempt}]" if attempt > 1 else ""
                return f"=== Agent {i + 1} [{agent_name}] ({label}){attempt_suffix} ===\n{result}"
            except asyncio.TimeoutError:
                last_error = f"[TIMEOUT after {timeout}s]"
                logger.warning(f"[agent {i+1}] attempt {attempt} timeout")
            except Exception as e:
                last_error = f"[ERROR] {type(e).__name__}: {e}"
                logger.warning(f"[agent {i+1}] attempt {attempt} failed: {e}")
            if attempt <= retries:
                await asyncio.sleep(2)

        return f"=== Agent {i + 1} [{agent_name}] ({label}) [failed after {retries+1} attempts] ===\n{last_error}"

    results = await asyncio.gather(*[run_one(i, a) for i, a in enumerate(agents)], return_exceptions=False)
    return "\n\n".join(results)


@mcp.tool()
async def waibee_models() -> str:
    """List all available models from Waibee gateway."""
    _check_enabled()
    models = await gateway.get_models()
    lines = [m["id"] for m in models if isinstance(m, dict) and "id" in m]
    return "\n".join(lines) if lines else "No models found"


@mcp.tool()
def waibee_log(n: int = 50, filter: str = None) -> str:
    """
    Show last N lines from waibee log. Use to check agent progress.
    filter: optional string to grep for (e.g. "agent", "ERROR", "step=")
    """
    from gateway import LOG_PATH
    if not LOG_PATH.exists():
        return "Log file not found"
    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    if filter:
        lines = [l for l in lines if filter in l]
    return "\n".join(lines[-n:])


@mcp.tool()
def waibee_toggle(enabled: bool = None) -> str:
    """
    Show status or toggle waibee_mcp.
    No args: show current status.
    enabled=True/False: enable or disable.
    """
    if enabled is None:
        status = "ENABLED" if is_enabled() else "DISABLED"
        cfg = get_config()
        key = get_api_key()
        key_preview = (key[:12] + "...") if key else "NOT SET"
        return (
            f"waibee_mcp: {status}\n"
            f"api_key: {key_preview}\n"
            f"models: {cfg['models']}\n"
            f"caveman_ultra: {cfg.get('caveman_ultra', True)}"
        )

    set_flag(enabled)
    return f"waibee_mcp {'ENABLED' if enabled else 'DISABLED'}"


@mcp.tool()
def waibee_stats(date_str: str = None) -> str:
    """
    Show token and cost stats.
    No args: today. date_str: specific day as YYYY-MM-DD.
    """
    label = date_str or str(date.today())
    data = stats_module.get_stats(date_str)
    return stats_module.format_stats(data, label)


if __name__ == "__main__":
    mcp.run()
