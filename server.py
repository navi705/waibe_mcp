import asyncio
import glob as glob_module
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import date
from pathlib import Path

import fastmcp
from fastmcp import Context
import logging

from config import get_config, get_api_key, is_enabled, set_flag
import db as db_module
import gateway
import stats as stats_module
from prompts import build_system_prompt

logger = logging.getLogger("waibee_mcp")

mcp = fastmcp.FastMCP("waibee")

# Background job handles — keeps tasks alive (GC won't collect them)
_RUNNING_JOBS: dict[str, asyncio.Task] = {}

# Orphan recovery on startup
_startup_orphans = db_module.recover_orphans(stale_after=240.0)
if _startup_orphans:
    logger.warning(f"[startup] orphaned jobs → interrupted: {_startup_orphans}")


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
async def waibee_digest(
    sources: list[str],
    task: str,
    complexity: str = "simple",
    model: str = None,
) -> str:
    """
    One-shot: read files/run commands → model summarizes → short result.
    Use to keep large content out of Claude Code context.
    For multi-step work use waibee_agent instead.
    sources: list of file paths or "cmd:<powershell command>"
    """
    _check_enabled()
    blobs = []
    for s in sources:
        if s.startswith("cmd:"):
            cmd = s[4:]
            out = await _run_bash(cmd)
            blobs.append(f"=== $ {cmd} ===\n{out}")
        else:
            p = Path(s)
            if not p.exists():
                blobs.append(f"[NOT FOUND: {s}]")
            else:
                blobs.append(f"=== {s} ===\n{p.read_text(encoding='utf-8', errors='replace')}")
    combined = "\n\n".join(blobs)
    resolved = _resolve_model(complexity, model)
    sys_prompt = _get_system_prompt("default")
    messages = [{"role": "user", "content": f"{task}\n\n{combined}"}]
    return await gateway.call(messages, resolved, sys_prompt)


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
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Replace exact text in a file (old_string → new_string). "
                "Faster than read_file + write_file — no need to read the whole file. "
                "FAILS if old_string not found or appears multiple times. "
                "Include surrounding lines to make old_string unique. "
                "Use replace_all=true to replace every occurrence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string", "description": "Exact text to find (2-4 lines for uniqueness)"},
                    "new_string": {"type": "string", "description": "Replacement text"},
                    "replace_all": {"type": "boolean", "description": "Replace all occurrences (default false)"},
                },
                "required": ["path", "old_string", "new_string"],
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
    r"(?<![/\\a-zA-Z:])python(\d(\.\d+)?)?\b",                              # any bare python not preceded by path (use py launcher or full path)
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


BASH_TIMEOUT = 30
KILL_GRACE = 3
TOOL_TIMEOUT = BASH_TIMEOUT + KILL_GRACE + 5  # outer safety net per tool call
GATEWAY_TIMEOUT = 300  # per gateway call
WALL_CLOCK_DEFAULT = 900  # total agent wall-clock cap (15 min)
MAX_STEPS_DEFAULT = 200   # safety fallback — wall_clock is the primary guard

_STORE_ALIAS_DIR = (os.environ.get("LOCALAPPDATA", "") + r"\Microsoft\WindowsApps").lower()


def _windows_kill_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _is_msstore_python_stub(command: str) -> bool:
    if not re.search(r"(?:^|[\s;&|(])(python|python3)(\.exe)?(?:\s|$)", command, re.IGNORECASE):
        return False
    resolved = shutil.which("python") or ""
    rl = resolved.lower()
    if _STORE_ALIAS_DIR and rl.startswith(_STORE_ALIAS_DIR):
        return True
    try:
        if resolved and os.path.getsize(resolved) == 0:
            return True
    except OSError:
        pass
    return False


async def _run_bash(command: str, workdir: str = None) -> str:
    if _is_msstore_python_stub(command):
        return (
            "[BLOCKED] 'python' resolves to MS Store app-execution alias — "
            "spawns unkillable broker process, hangs forever. Use 'py' launcher: "
            "'py script.py', 'py -m pytest'."
        )
    try:
        # Prepend UTF-8 output encoding — prevents Cyrillic/Unicode mangling on Russian Windows
        utf8_prefix = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; $OutputEncoding = [Text.Encoding]::UTF8; "
        proc = await asyncio.create_subprocess_exec(
            "powershell", "-NoProfile", "-NonInteractive", "-Command", utf8_prefix + command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
        )
    except Exception as e:
        return f"[ERROR] failed to start: {e}"

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=BASH_TIMEOUT)
    except asyncio.TimeoutError:
        if sys.platform == "win32":
            _windows_kill_tree(proc.pid)
        else:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(proc.communicate(), timeout=KILL_GRACE)
        except Exception:
            pass
        return f"[TIMEOUT] command exceeded {BASH_TIMEOUT}s — killed. Avoid long-running or interactive commands."

    out = stdout.decode("utf-8", "replace")
    err = stderr.decode("utf-8", "replace")
    if err:
        out += f"\nSTDERR:\n{err}"
    return out or f"(exit {proc.returncode})"


def _check_bash_blocklist(command: str) -> str | None:
    """Return matched pattern string if command is blocked, else None."""
    for pattern, compiled in zip(BASH_BLOCKLIST, _BASH_BLOCKLIST_RE):
        if compiled.search(command):
            return pattern
    return None


async def _exec_tool(name: str, args: dict, workdir: str = None, allow_dangerous: bool = False) -> str:
    try:
        if name == "read_file":
            p = Path(args["path"])
            try:
                if p.stat().st_size > 10 * 1024 * 1024:
                    return f"[TOO LARGE] {p.stat().st_size} bytes — use grep_search instead"
            except OSError:
                pass
            return await asyncio.wait_for(
                asyncio.to_thread(lambda: p.read_text(encoding="utf-8", errors="replace")),
                timeout=10,
            )

        elif name == "write_file":
            p = Path(args["path"])
            if workdir:
                wd = Path(workdir).resolve()
                try:
                    p.resolve().relative_to(wd)
                except ValueError:
                    return f"[DENIED] write_file outside workdir: {p}"
            content = args["content"]
            await asyncio.wait_for(
                asyncio.to_thread(lambda: (p.parent.mkdir(parents=True, exist_ok=True), p.write_text(content, encoding="utf-8"))),
                timeout=10,
            )
            return f"Written {len(content)} chars to {p}"

        elif name == "bash_run":
            if not allow_dangerous:
                matched = _check_bash_blocklist(args["command"])
                if matched is not None:
                    return (
                        f"[BLOCKED] dangerous command: {matched}. "
                        "Use workdir restrictions or request user confirmation."
                    )
            return await _run_bash(args["command"], workdir)

        elif name == "glob_search":
            root = args.get("root", workdir or ".")
            pattern = args["pattern"]
            return await asyncio.wait_for(
                asyncio.to_thread(lambda: "\n".join(glob_module.glob(pattern, root_dir=root, recursive=True)) or "(no matches)"),
                timeout=15,
            )

        elif name == "grep_search":
            cmd = ["rg", "--line-number", args["pattern"], args["path"]]
            if args.get("file_glob"):
                cmd += ["--glob", args["file_glob"]]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
                return stdout.decode("utf-8", "replace") or "(no matches)"
            except asyncio.TimeoutError:
                if sys.platform == "win32":
                    _windows_kill_tree(proc.pid)
                return "[TIMEOUT] grep_search exceeded 25s"

        elif name == "list_dir":
            path = args["path"]
            return await asyncio.wait_for(
                asyncio.to_thread(lambda: "\n".join(os.listdir(path))),
                timeout=10,
            )

        elif name == "edit_file":
            p = Path(args["path"])
            if workdir:
                wd = Path(workdir).resolve()
                try:
                    p.resolve().relative_to(wd)
                except ValueError:
                    return f"[DENIED] edit_file outside workdir: {p}"
            old_string = args["old_string"]
            new_string = args["new_string"]
            replace_all_flag = args.get("replace_all", False)
            content = await asyncio.wait_for(
                asyncio.to_thread(lambda: p.read_text(encoding="utf-8", errors="replace")),
                timeout=10,
            )
            count = content.count(old_string)
            if count == 0:
                return f"[ERROR] edit_file: old_string not found in {p}"
            if count > 1 and not replace_all_flag:
                return (
                    f"[ERROR] edit_file: old_string found {count} times in {p}. "
                    "Add more surrounding context to make it unique, or pass replace_all=true."
                )
            new_content = content.replace(old_string, new_string, -1 if replace_all_flag else 1)
            await asyncio.wait_for(
                asyncio.to_thread(lambda: p.write_text(new_content, encoding="utf-8")),
                timeout=10,
            )
            replaced = count if replace_all_flag else 1
            return f"Replaced {replaced} occurrence(s) in {p}"

        else:
            return f"[UNKNOWN TOOL: {name}]"

    except asyncio.TimeoutError:
        return f"[TIMEOUT] {name} exceeded time limit"
    except Exception as e:
        return f"[ERROR] {name}: {e}"


CHECKPOINT_EVERY = 1  # save checkpoint every step for crash recovery

TOOL_RESULTS_DIR = Path(__file__).parent / "tool_results"
TOOL_RESULTS_DIR.mkdir(exist_ok=True)
MAX_TOOL_RESULT = 8000
LARGE_RESULT_PREVIEW = 2048


async def _notify(ctx: "Context | None", msg: str) -> None:
    if ctx is None:
        return
    try:
        await ctx.info(msg)
    except Exception:
        pass


async def _run_agent_loop(
    task: str,
    model: str,
    sys_prompt: str,
    thinking_effort: str = None,
    max_steps: int = MAX_STEPS_DEFAULT,
    workdir: str = None,
    context: str = None,
    allow_dangerous: bool = False,
    wall_clock_s: int = WALL_CLOCK_DEFAULT,
    agent_id: str = None,
    job_id: str = None,
    resume_messages: list = None,
    ctx: "Context | None" = None,
) -> str:
    if resume_messages:
        messages = list(resume_messages)
    else:
        content = task
        if context:
            content = f"Context:\n{context}\n\nTask:\n{task}"
        messages = [{"role": "user", "content": content}]

    total_cost = 0.0
    step = 0
    deadline = time.monotonic() + wall_clock_s
    aid = agent_id or "agent"
    _repeat_counts: dict[str, int] = {}  # (tool:args_hash) → count for loop detection

    # Persist initial messages — crash at step 1 still recoverable
    if job_id:
        for i, msg in enumerate(messages):
            await asyncio.to_thread(db_module.append_message, job_id, i, msg)

    resp = None
    while step < max_steps:
        if time.monotonic() > deadline:
            logger.warning(f"[{aid}] wall-clock {wall_clock_s}s exceeded at step {step}")
            if job_id:
                await asyncio.to_thread(db_module.finish_job, job_id, "timeout")
            return f"[TIMEOUT] agent wall-clock limit {wall_clock_s}s reached at step {step}.\n\n[steps: {step}, cost: ${total_cost:.6f}]"

        step += 1
        if job_id:
            await asyncio.to_thread(db_module.touch_heartbeat, job_id)
        await _notify(ctx, f"[{aid}] step {step}/{max_steps} — thinking...")

        try:
            resp = await asyncio.wait_for(
                gateway.call_with_tools(
                    messages=messages,
                    model=model,
                    tools=AGENT_TOOLS,
                    system_prompt=sys_prompt,
                    thinking_effort=thinking_effort,
                ),
                timeout=GATEWAY_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[{aid}] step={step} gateway timeout after {GATEWAY_TIMEOUT}s")
            if job_id:
                await asyncio.to_thread(db_module.finish_job, job_id, "timeout", error=f"gateway timeout at step {step}")
            return f"[TIMEOUT] gateway did not respond within {GATEWAY_TIMEOUT}s at step {step}.\n\n[steps: {step}, cost: ${total_cost:.6f}]"
        except Exception as e:
            logger.error(f"[{aid}] step={step} gateway error: {e}")
            if job_id:
                await asyncio.to_thread(db_module.finish_job, job_id, "failed", error=str(e))
            return f"[ERROR] gateway: {e}\n\n[steps: {step}, cost: ${total_cost:.6f}]"

        total_cost += resp["cost"]
        if job_id:
            await asyncio.to_thread(db_module.update_job_step, job_id, step, resp["cost"])

        assistant_msg = {"role": "assistant"}
        if resp["content"]:
            assistant_msg["content"] = resp["content"]
        if resp["tool_calls"]:
            assistant_msg["tool_calls"] = resp["tool_calls"]
        messages.append(assistant_msg)
        if job_id:
            await asyncio.to_thread(db_module.append_message, job_id, len(messages) - 1, assistant_msg)

        if resp["finish_reason"] != "tool_calls" or not resp["tool_calls"]:
            break

        for tc in resp["tool_calls"]:
            fn = tc["function"]
            name = fn["name"]
            try:
                args = json.loads(fn["arguments"])
            except Exception:
                args = {}
            args_preview = str(args)[:500]
            logger.info(f"[{aid}] step={step} tool={name} args={args_preview}")
            await _notify(ctx, f"[{aid}] step {step} → {name}({str(args)[:80]})")
            try:
                result = await asyncio.wait_for(
                    _exec_tool(name, args, workdir, allow_dangerous),
                    timeout=TOOL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                result = f"[TOOL TIMEOUT] {name} exceeded {TOOL_TIMEOUT}s — skipped"
                logger.warning(f"[{aid}] step={step} tool={name} outer timeout")
            result_preview = result[:1000]
            logger.info(f"[{aid}] step={step} tool={name} result={result_preview}")
            await _notify(ctx, f"[{aid}] step {step} ← {name}: {result_preview}")
            if job_id:
                await asyncio.to_thread(
                    db_module.append_trace, job_id, aid, step, name, args_preview, result_preview
                )
            if len(result) > MAX_TOOL_RESULT:
                fname = f"{job_id or uuid.uuid4().hex[:8]}-s{step}-{name}-{tc['id'][:8]}.txt"
                result_file = TOOL_RESULTS_DIR / fname
                try:
                    result_file.write_text(result, encoding="utf-8")
                    preview = result[:LARGE_RESULT_PREVIEW]
                    result = (
                        f"[Result too large ({len(result)} chars). "
                        f"Full output saved to: {result_file}\n"
                        f"Preview (first {LARGE_RESULT_PREVIEW} chars):\n{preview}"
                    )
                except Exception:
                    result = result[:MAX_TOOL_RESULT] + f"\n...[truncated, total {len(result)} chars]"
            tool_msg = {"role": "tool", "tool_call_id": tc["id"], "content": result}
            messages.append(tool_msg)
            if job_id:
                await asyncio.to_thread(db_module.append_message, job_id, len(messages) - 1, tool_msg)

            # Loop detection: same tool+args repeated 3+ times → warn agent
            repeat_key = f"{name}:{fn['arguments']}"
            _repeat_counts[repeat_key] = _repeat_counts.get(repeat_key, 0) + 1
            if _repeat_counts[repeat_key] == 3:
                warn = (
                    f"WARNING: You have called {name}() with the same arguments {_repeat_counts[repeat_key]} times "
                    f"and keep getting the same result. This approach is not working. "
                    f"Stop retrying. Try a completely different strategy or explain why the task cannot be completed."
                )
                warn_msg = {"role": "user", "content": warn}
                messages.append(warn_msg)
                if job_id:
                    await asyncio.to_thread(db_module.append_message, job_id, len(messages) - 1, warn_msg)
                logger.warning(f"[{aid}] loop detected: {name} called 3x with same args")

    final = resp.get("content") if resp else None
    if not final:
        logger.warning(f"[{aid}] max_steps={max_steps} reached, requesting summary")
        messages.append({"role": "user", "content": "You have reached the step limit. Summarize what you have done so far and what remains."})
        try:
            summary_resp = await asyncio.wait_for(
                gateway.call_with_tools(
                    messages=messages, model=model, tools=AGENT_TOOLS,
                    system_prompt=sys_prompt, thinking_effort=thinking_effort,
                ),
                timeout=GATEWAY_TIMEOUT,
            )
            total_cost += summary_resp["cost"]
            final = summary_resp.get("content") or "(no response — max steps reached)"
        except Exception as e:
            final = f"(summary failed: {e})"
        if job_id:
            await asyncio.to_thread(db_module.finish_job, job_id, "interrupted", result=final)
    else:
        if job_id:
            await asyncio.to_thread(db_module.finish_job, job_id, "done", result=final)

    logger.info(f"[{aid}] done steps={step} cost=${total_cost:.6f}")
    return f"{final}\n\n[steps: {step}, cost: ${total_cost:.6f}]"


def _filter_resume_messages(messages: list) -> list:
    """Strip orphaned tool_calls (no matching tool result) and whitespace-only assistant messages."""
    result_ids = {msg.get("tool_call_id") for msg in messages if msg.get("role") == "tool"}
    filtered = []
    for msg in messages:
        if msg.get("role") == "assistant":
            content = (msg.get("content") or "").strip()
            tool_calls = msg.get("tool_calls") or []
            if not content and not tool_calls:
                continue
            if tool_calls:
                resolved = [tc for tc in tool_calls if tc.get("id") in result_ids]
                if not resolved:
                    if not content:
                        continue
                    msg = {k: v for k, v in msg.items() if k != "tool_calls"}
                elif len(resolved) != len(tool_calls):
                    msg = {**msg, "tool_calls": resolved}
        filtered.append(msg)
    return filtered


async def _run_agent_loop_guarded(*args, **kwargs) -> str:
    job_id = kwargs.get("job_id")
    aid = kwargs.get("agent_id") or "agent"
    try:
        return await _run_agent_loop(*args, **kwargs)
    except asyncio.CancelledError:
        logger.warning(f"[{aid}] CancelledError — marking interrupted")
        if job_id:
            try:
                await asyncio.to_thread(db_module.finish_job, job_id, "interrupted", error="cancelled by client")
            except Exception:
                pass
        raise


async def _supervise(job_id: str, coro) -> None:
    try:
        await coro
    except asyncio.CancelledError:
        await asyncio.to_thread(db_module.finish_job, job_id, "interrupted", error="cancelled")
        raise
    except Exception as e:
        await asyncio.to_thread(db_module.finish_job, job_id, "failed", error=f"{type(e).__name__}: {e}")
    finally:
        _RUNNING_JOBS.pop(job_id, None)


@mcp.tool()
async def waibee_agent(
    task: str,
    ctx: Context,
    complexity: str = "medium",
    model: str = None,
    thinking_effort: str = None,
    agent: str = "default",
    max_steps: int = MAX_STEPS_DEFAULT,
    workdir: str = None,
    context: str = None,
    system_prompt: str = None,
    allow_dangerous: bool = False,
    wait: bool = True,
) -> str:
    """
    Agentic loop: model autonomously reads/writes files and runs commands until task is done.
    complexity: simple|medium|complex
    thinking_effort: low|medium|high (use with complex/opus for hard problems)
    workdir: restrict write_file to this directory
    context: optional chat context to inject
    wait: True (default) = block until done. False = return job_id immediately, poll with waibee_job_status.
    After wait=False, call waibee_job_wait(job_id) to block and receive live step notifications.
    allow_dangerous: set True to bypass bash_run blocklist (default False). Blocklist blocks
        destructive commands: git push, rm -rf, Remove-Item -Recurse -Force, format, diskpart,
        DROP TABLE/DATABASE, del /f /s, rd /s /q, shutdown, restart-computer, and any git
        command with --force.
    """
    _check_enabled()
    resolved = _resolve_model(complexity, model)
    sys_prompt = _get_system_prompt(agent, system_prompt)
    job_id = uuid.uuid4().hex[:12]
    agent_name = agent
    await asyncio.to_thread(db_module.create_job, job_id, task, resolved, agent_name, workdir)

    coro = _run_agent_loop_guarded(
        task, resolved, sys_prompt, thinking_effort, max_steps, workdir, context,
        allow_dangerous, agent_id=agent_name, job_id=job_id,
        ctx=None if not wait else ctx,
    )

    if not wait:
        task_handle = asyncio.create_task(_supervise(job_id, coro))
        _RUNNING_JOBS[job_id] = task_handle
        return json.dumps({"job_id": job_id, "status": "running", "hint": f"waibee_job_status('{job_id}')"})

    return await coro


@mcp.tool()
async def waibee_agents(
    agents: list[dict],
    max_steps: int = MAX_STEPS_DEFAULT,
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

        agent_id = f"a{i+1}:{agent_name}"
        job_id = uuid.uuid4().hex[:12]
        await asyncio.to_thread(db_module.create_job, job_id, item["task"], resolved, agent_name, item.get("workdir"))
        last_error = None
        for attempt in range(1, retries + 2):
            try:
                result = await asyncio.wait_for(
                    _run_agent_loop_guarded(
                        task=item["task"],
                        model=resolved,
                        sys_prompt=sys_prompt,
                        thinking_effort=item.get("thinking_effort"),
                        max_steps=item.get("max_steps", max_steps),
                        workdir=item.get("workdir"),
                        context=item.get("context"),
                        allow_dangerous=item_allow_dangerous,
                        wall_clock_s=item.get("wall_clock_s", WALL_CLOCK_DEFAULT),
                        agent_id=agent_id,
                        job_id=job_id,
                    ),
                    timeout=timeout,
                )
                attempt_suffix = f" [attempt {attempt}]" if attempt > 1 else ""
                return f"=== Agent {i + 1} [{agent_name}] ({label}){attempt_suffix} ===\n{result}"
            except asyncio.TimeoutError:
                last_error = f"[TIMEOUT after {timeout}s]"
                logger.warning(f"[{agent_id}] attempt {attempt} timeout")
            except Exception as e:
                last_error = f"[ERROR] {type(e).__name__}: {e}"
                logger.warning(f"[{agent_id}] attempt {attempt} failed: {e}")
            if attempt <= retries:
                await asyncio.sleep(2)

        return f"=== Agent {i + 1} [{agent_name}] ({label}) [failed after {retries+1} attempts] ===\n{last_error}"

    results = await asyncio.gather(*[run_one(i, a) for i, a in enumerate(agents)], return_exceptions=False)
    return "\n\n".join(results)


@mcp.tool()
def waibee_job_status(job_id: str) -> str:
    """
    Status + recent trace of a background job.
    Use to check progress of a job started with wait=False.
    """
    j = db_module.get_job(job_id)
    if not j:
        return f"[no such job: {job_id}]"
    trace = db_module.get_trace(job_id, limit=8)
    trace_lines = [
        f"  {t['step']}  {t['tool']}({t['args_preview']}) → {t['result_preview']}"
        for t in trace
    ]
    return json.dumps({
        "job_id": job_id,
        "status": j["status"],
        "step": j["last_step"],
        "cost": round(j["cost"], 6),
        "error": j["error"],
        "recent_trace": trace_lines,
    }, indent=2)


@mcp.tool()
def waibee_job_result(job_id: str) -> str:
    """Final result of a completed job. Returns result text or error."""
    j = db_module.get_job(job_id)
    if not j:
        return f"[no such job: {job_id}]"
    if j["status"] == "running":
        return json.dumps({"status": "running", "step": j["last_step"]})
    return j["result"] or j["error"] or "(no result)"


@mcp.tool()
def waibee_jobs(status: str = None) -> str:
    """
    List recent jobs.
    status filter: running|done|failed|timeout|interrupted|cancelled
    No filter: all recent jobs.
    """
    jobs = db_module.list_jobs(status=status, limit=20)
    rows = [{
        "job_id": j["job_id"],
        "status": j["status"],
        "step": j["last_step"],
        "task": (j["task"] or "")[:80],
        "cost": round(j["cost"], 6),
    } for j in jobs]
    return json.dumps(rows, indent=2)


@mcp.tool()
def waibee_job_cancel(job_id: str) -> str:
    """Cancel a running background job."""
    handle = _RUNNING_JOBS.get(job_id)
    if handle:
        handle.cancel()
        _RUNNING_JOBS.pop(job_id, None)
    db_module.finish_job(job_id, "cancelled")
    return f"cancelled {job_id}"


@mcp.tool()
async def waibee_resume(job_id: str, extra_steps: int = 40) -> str:
    """
    Resume an interrupted job from its last checkpoint.
    Use after a job shows status=interrupted (hit max_steps or crashed).
    """
    _check_enabled()
    j = db_module.get_job(job_id)
    if not j:
        return f"[no such job: {job_id}]"
    if j["status"] == "running":
        return f"[job {job_id} is still running — use waibee_job_status to check]"

    prior_messages = db_module.get_resume_messages(job_id, head=2, tail=20)
    if not prior_messages:
        return f"[no messages found for {job_id} — cannot resume]"

    prior_messages = _filter_resume_messages(prior_messages)

    primer = (
        "RESUMING FROM CHECKPOINT. Continue the original task. "
        "The messages above are your most recent context from before the interruption."
    )
    resume_messages = prior_messages + [{"role": "user", "content": primer}]

    resolved = j["model"]
    sys_prompt = _get_system_prompt(j["agent_name"] or "default")
    new_job_id = uuid.uuid4().hex[:12]
    await asyncio.to_thread(
        db_module.create_job, new_job_id, j["task"], resolved,
        j["agent_name"] or "default", j["workdir"], parent_id=job_id,
    )

    return await _run_agent_loop_guarded(
        task=j["task"],
        model=resolved,
        sys_prompt=sys_prompt,
        max_steps=extra_steps,
        workdir=j["workdir"],
        agent_id=f"resume:{job_id[:8]}",
        job_id=new_job_id,
        resume_messages=resume_messages,
    )


@mcp.tool()
async def waibee_job_wait(job_id: str, ctx: Context) -> str:
    """
    Attach to a background job and receive live step notifications via ctx.info().
    Returns when job completes. Use after waibee_agent(wait=False).
    """
    j = db_module.get_job(job_id)
    if not j:
        return f"[no such job: {job_id}]"
    if j["status"] != "running":
        return j["result"] or j["error"] or j["status"]

    seen_ids: set[int] = set()
    # seed with already-seen trace so we don't re-emit past entries
    for t in db_module.get_trace(job_id, limit=100):
        seen_ids.add(t["id"])

    last_notify = time.monotonic()
    while True:
        await asyncio.sleep(2)
        j = db_module.get_job(job_id)
        if not j:
            return "[job disappeared]"

        # emit new trace entries (get_trace returns newest-first, reverse to emit in order)
        new_trace = [t for t in db_module.get_trace(job_id, limit=20) if t["id"] not in seen_ids]
        for t in reversed(new_trace):
            seen_ids.add(t["id"])
            await _notify(ctx, f"step {t['step']} -> {t['tool']}({t['args_preview'][:60]}) <- {t['result_preview'][:80]}")
            last_notify = time.monotonic()

        if j["status"] != "running":
            result = j["result"] or j["error"] or j["status"]
            await _notify(ctx, f"[done] {j['status']} - {result[:100]}")
            return result

        # heartbeat if no new trace for 20s
        if time.monotonic() - last_notify > 20:
            await _notify(ctx, f"[waiting] job {job_id} still running... step {j['last_step']}")
            last_notify = time.monotonic()


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
