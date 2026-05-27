import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

CONFIG_PATH = Path(__file__).parent / "config.json"
FLAG_PATH = Path.home() / ".claude" / "waibee_mcp.json"


def get_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_api_key() -> str:
    return os.getenv("API_KEY", "")


def get_flag() -> dict:
    if FLAG_PATH.exists():
        with open(FLAG_PATH) as f:
            return json.load(f)
    return {"enabled": False}


def set_flag(enabled: bool) -> dict:
    FLAG_PATH.parent.mkdir(exist_ok=True)
    data = {"enabled": enabled}
    with open(FLAG_PATH, "w") as f:
        json.dump(data, f)
    return data


def is_enabled() -> bool:
    return get_flag().get("enabled", False)
