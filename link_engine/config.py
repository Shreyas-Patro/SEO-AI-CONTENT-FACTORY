import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_config = None


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    cfg["anthropic_api_key"] = os.getenv("ANTHROPIC_API_KEY", "")
    return cfg


def get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config