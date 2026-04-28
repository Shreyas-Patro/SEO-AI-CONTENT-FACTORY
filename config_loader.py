"""
Config loader — reads config.yaml and provides access throughout the app.
"""

import yaml
import os

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

def load_config(path=_CONFIG_PATH):
    with open(path, "r") as f:
        return yaml.safe_load(f)

cfg = load_config()

# Alias so both names work
def get_config():
    return cfg

def get_anthropic_key():
    return cfg["anthropic"]["api_key"]

def get_serpapi_key():
    return cfg["serpapi"]["api_key"]

def get_model(role="bulk"):
    """role: 'writer', 'architect', or 'bulk'"""
    return cfg["anthropic"]["models"][role]

def get_path(name):
    return cfg["paths"][name]
