import json
from datetime import date
from pathlib import Path

STATS_PATH = Path.home() / ".claude" / "waibee_stats.json"


def _load() -> dict:
    if STATS_PATH.exists():
        with open(STATS_PATH) as f:
            return json.load(f)
    return {}


def _save(stats: dict):
    STATS_PATH.parent.mkdir(exist_ok=True)
    with open(STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2)


def record(model: str, input_tokens: int, output_tokens: int, cost: float):
    stats = _load()
    today = str(date.today())

    if today not in stats:
        stats[today] = {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "by_model": {},
        }

    day = stats[today]
    day["requests"] += 1
    day["input_tokens"] += input_tokens
    day["output_tokens"] += output_tokens
    day["cost_usd"] = round(day["cost_usd"] + cost, 8)

    if model not in day["by_model"]:
        day["by_model"][model] = {
            "requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
        }

    m = day["by_model"][model]
    m["requests"] += 1
    m["input_tokens"] += input_tokens
    m["output_tokens"] += output_tokens
    m["cost_usd"] = round(m["cost_usd"] + cost, 8)

    _save(stats)


def get_stats(target_date: str = None) -> dict:
    stats = _load()
    key = target_date or str(date.today())
    return stats.get(key, {})


def format_stats(data: dict, label: str) -> str:
    if not data:
        return f"No data for {label}"

    lines = [
        f"Stats [{label}]",
        f"  requests : {data.get('requests', 0)}",
        f"  input    : {data.get('input_tokens', 0):,} tokens",
        f"  output   : {data.get('output_tokens', 0):,} tokens",
        f"  cost     : ${data.get('cost_usd', 0):.6f}",
    ]

    by_model = data.get("by_model", {})
    if by_model:
        lines.append("\n  by model:")
        for model, m in by_model.items():
            lines.append(
                f"    {model}: req={m['requests']} "
                f"in={m['input_tokens']:,} out={m['output_tokens']:,} "
                f"${m['cost_usd']:.6f}"
            )

    return "\n".join(lines)
