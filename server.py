import asyncio
import glob as glob_module
import json
import os
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


def _exec_tool(name: str, args: dict, workdir: str = None) -> str:
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
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", args["command"]],
                capture_output=True, text=True, timeout=60,
                cwd=workdir,
            )
            out = result.stdout
            if result.stderr:
                out += f"\nSTDERR:\n{result.stderr}"
            return out or f"(exit {result.returncode})"

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
    max_steps: int = 20,
    workdir: str = None,
    context: str = None,
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
            result = await asyncio.to_thread(_exec_tool, name, args, workdir)
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

    final = resp.get("content") or "(no response)"
    logger.info(f"[agent] done steps={step} cost=${total_cost:.6f}")
    return f"{final}\n\n[steps: {step}, cost: ${total_cost:.6f}]"


@mcp.tool()
async def waibee_agent(
    task: str,
    complexity: str = "medium",
    model: str = None,
    thinking_effort: str = None,
    agent: str = "default",
    max_steps: int = 20,
    workdir: str = None,
    context: str = None,
    system_prompt: str = None,
) -> str:
    """
    Agentic loop: model autonomously reads/writes files and runs commands until task is done.
    complexity: simple|medium|complex
    thinking_effort: low|medium|high (use with complex/opus for hard problems)
    workdir: restrict write_file to this directory
    context: optional chat context to inject
    """
    _check_enabled()
    resolved = _resolve_model(complexity, model)
    sys_prompt = _get_system_prompt(agent, system_prompt)
    return await _run_agent_loop(task, resolved, sys_prompt, thinking_effort, max_steps, workdir, context)


@mcp.tool()
async def waibee_agents(
    agents: list[dict],
    max_steps: int = 20,
) -> str:
    """
    Run multiple independent agentic loops in parallel.
    Each agent dict: {task, complexity?, model?, thinking_effort?, agent?, workdir?, context?, system_prompt?}
    """
    _check_enabled()

    async def run_one(i: int, item: dict) -> str:
        resolved = _resolve_model(item.get("complexity", "medium"), item.get("model"))
        sys_prompt = _get_system_prompt(item.get("agent", "default"), item.get("system_prompt"))
        label = item.get("task", "")[:60]
        agent_name = item.get("agent", "default")
        timeout = item.get("timeout", 300)
        retries = item.get("retries", 1)

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
