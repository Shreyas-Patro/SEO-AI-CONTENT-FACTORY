"""
Config loader — reads config.yaml and substitutes ${ENV_VAR} placeholders.
"""
import os
import re
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_CONFIG_PATH = Path(__file__).parent / "config.yaml"
_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _interpolate(obj):
    if isinstance(obj, str):
        return _ENV_PATTERN.sub(lambda m: os.getenv(m.group(1), ""), obj)
    if isinstance(obj, dict):
        return {k: _interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_interpolate(v) for v in obj]
    return obj


def load_config(path=_CONFIG_PATH):
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    return _interpolate(raw)


cfg = load_config()


def get_config():
    return cfg


def get_anthropic_key():
    key = cfg["anthropic"]["api_key"]
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY missing — check .env")
    return key


def get_serpapi_key():
    key = cfg["serpapi"]["api_key"]
    if not key:
        raise RuntimeError("SERPAPI_API_KEY missing — check .env")
    return key


def get_perplexity_key():
    return cfg.get("perplexity", {}).get("api_key", "")


def get_model(role="bulk"):
    return cfg["anthropic"]["models"][role]


def get_path(name):
    return cfg["paths"][name]


def current_year():
    return cfg["defaults"]["current_year"]