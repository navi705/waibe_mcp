import httpx
from config import get_config, get_api_key
import stats as stats_module

THINKING_BUDGETS = {"low": 1000, "medium": 5000, "high": 10000}


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

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{gateway_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    usage = data.get("usage", {})
    cost = (usage.get("cost_details") or {}).get("upstream_inference_cost", 0) or 0
    stats_module.record(
        model=model,
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        cost=cost,
    )

    return data["choices"][0]["message"]["content"]


async def get_models() -> list:
    cfg = get_config()
    gateway_url = cfg.get("gateway_url", "https://gateway.waibee.com/api/v1")
    api_key = get_api_key()

    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{gateway_url}/models", headers=headers)
        resp.raise_for_status()
        data = resp.json()

    return data.get("data", [])
