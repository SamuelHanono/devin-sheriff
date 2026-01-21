import json
import os
from pathlib import Path
from pydantic import BaseModel
from typing import Optional

CONFIG_DIR = Path.home() / ".devin-sheriff"
CONFIG_FILE = CONFIG_DIR / "config.json"

class AppConfig(BaseModel):
    github_token: Optional[str] = None
    devin_api_key: Optional[str] = None
    # Default Devin API URL (placeholder - adjust if using a specific proxy)
    devin_api_url: str = "https://api.devin.ai/v1" 

def load_config() -> AppConfig:
    """Loads config from local file or returns empty config."""
    if not CONFIG_FILE.exists():
        return AppConfig()
    
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
            return AppConfig(**data)
    except Exception:
        return AppConfig()

def save_config(config: AppConfig):
    """Saves config to local file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        f.write(config.model_dump_json(indent=2))

def get_config_or_fail() -> AppConfig:
    """Helper to get config or raise error if not set up."""
    config = load_config()
    if not config.github_token or not config.devin_api_key:
        raise ValueError("Configuration missing. Please run 'python main.py setup' first.")
    return config