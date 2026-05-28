import httpx
import logging
import logging.handlers
from pathlib import Path
from config import get_config, get_api_key
import stats as stats_module

THINKING_BUDGETS = {"low": 1000, "medium": 5000, "high": 10000}

LOG_PATH = Path(__file__).parent / "logs" / "waibee_mcp.log"
LOG_PATH.parent.mkdir(exist_ok=True)

logger = logging.getLogger("waibee_mcp")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)


async def call(
    messages: list,
    model: str,
    system_prompt: str = None,
    thinking_effort: str = None,
    max_tokens: int = 8192,
) -> str:
    cfg = get_config()
    gateway_url = cfg.get("gateway_url", "https://gateway.waibee.com/api/v1")
    api_key = get_api_key()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "Waibee",
    }

    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(messages)

    payload = {
        "model": model,
        "messages": msgs,
        "max_tokens": max_tokens,
        "stream": False,
    }

    if thinking_effort:
        budget = THINKING_BUDGETS.get(thinking_effort, 5000)
        if model.startswith("anthropic/"):
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        else:
            payload["reasoning"] = {"effort": thinking_effort}

    logger.info(f"→ {model} | thinking={thinking_effort} | msgs={len(msgs)}")

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{gateway_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            logger.info(f"← status={resp.status_code} | model={model}")

            if resp.status_code != 200:
                logger.error(f"Gateway error {resp.status_code}: {resp.text}")
                resp.raise_for_status()

            data = resp.json()

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTPStatusError: {e.response.status_code} — {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Request failed: {type(e).__name__}: {e}")
        raise

    usage = data.get("usage", {})
    cost = (usage.get("cost_details") or {}).get("upstream_inference_cost", 0) or 0
    logger.info(
        f"✓ in={usage.get('prompt_tokens', 0)} out={usage.get('completion_tokens', 0)} "
        f"cost=${cost:.6f}"
    )

    stats_module.record(
        model=model,
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        cost=cost,
    )

    return data["choices"][0]["message"]["content"]


async def call_with_tools(
    messages: list,
    model: str,
    tools: list[dict],
    system_prompt: str = None,
    thinking_effort: str = None,
    max_tokens: int = 8192,
) -> dict:
    cfg = get_config()
    gateway_url = cfg.get("gateway_url", "https://gateway.waibee.com/api/v1")
    api_key = get_api_key()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "Waibee",
    }

    msgs = []
    if system_prompt:
        msgs.append({"role": "system", "content": system_prompt})
    msgs.extend(messages)

    payload = {
        "model": model,
        "messages": msgs,
        "max_tokens": max_tokens,
        "stream": False,
        "tools": tools,
        "tool_choice": "auto",
    }

    if thinking_effort:
        budget = THINKING_BUDGETS.get(thinking_effort, 5000)
        if model.startswith("anthropic/"):
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        else:
            payload["reasoning"] = {"effort": thinking_effort}

    logger.info(f"→ {model} [tools] | thinking={thinking_effort} | msgs={len(msgs)}")

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{gateway_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            logger.info(f"← status={resp.status_code} | model={model}")

            if resp.status_code != 200:
                logger.error(f"Gateway error {resp.status_code}: {resp.text}")
                resp.raise_for_status()

            data = resp.json()

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTPStatusError: {e.response.status_code} — {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Request failed: {type(e).__name__}: {e}")
        raise

    usage = data.get("usage", {})
    cost = (usage.get("cost_details") or {}).get("upstream_inference_cost", 0) or 0
    logger.info(
        f"✓ in={usage.get('prompt_tokens', 0)} out={usage.get('completion_tokens', 0)} "
        f"cost=${cost:.6f}"
    )

    stats_module.record(
        model=model,
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        cost=cost,
    )

    choice = data["choices"][0]
    message = choice["message"]
    finish_reason = choice.get("finish_reason", "stop")

    return {
        "content": message.get("content"),
        "tool_calls": message.get("tool_calls") or [],
        "finish_reason": finish_reason,
        "usage": usage,
        "cost": cost,
    }


async def get_models() -> list:
    cfg = get_config()
    gateway_url = cfg.get("gateway_url", "https://gateway.waibee.com/api/v1")
    api_key = get_api_key()

    headers = {"Authorization": f"Bearer {api_key}"}
    logger.info("→ GET /models")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{gateway_url}/models", headers=headers)
            logger.info(f"← /models status={resp.status_code}")
            if resp.status_code != 200:
                logger.error(f"Models error {resp.status_code}: {resp.text}")
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"get_models failed: {e}")
        raise

    return data.get("data", [])
