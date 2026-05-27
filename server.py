import asyncio
import subprocess
from datetime import date
from pathlib import Path

import fastmcp

from config import get_config, get_api_key, is_enabled, set_flag
import gateway
import stats as stats_module
from prompts import build_system_prompt

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
    model: str = None,
    agent: str = "default",
) -> str:
    """
    Read files and process with model. Saves Claude Code input tokens.
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

    resolved = _resolve_model("simple", model)
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
        command, shell=True, capture_output=True, text=True, timeout=60
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
        resolved = _resolve_model(item.get("complexity", complexity), item.get("model"))
        sys_prompt = _get_system_prompt(item.get("agent", agent))
        messages = [{"role": "user", "content": task}]
        result = await gateway.call(messages, resolved, sys_prompt)
        return f"=== Subtask {i + 1} ===\n{result}"

    results = await asyncio.gather(*[run_one(i, t) for i, t in enumerate(subtasks)])
    return "\n\n".join(results)


@mcp.tool()
async def waibee_models() -> str:
    """List all available models from Waibee gateway."""
    _check_enabled()
    models = await gateway.get_models()
    lines = [m["id"] for m in models if isinstance(m, dict) and "id" in m]
    return "\n".join(lines) if lines else "No models found"


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
