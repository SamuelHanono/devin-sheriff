import json
import os
import shutil
import logging
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field

# --- LOGGING SETUP ---
# Simple logger to help debug config issues without cluttering stdout too much
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("config")

# 1. Define Paths
CONFIG_DIR = Path.home() / ".devin-sheriff"
CONFIG_FILE = CONFIG_DIR / "config.json"
BACKUP_FILE = CONFIG_DIR / "config.json.bak"

# 2. Define the Configuration Model
class AppConfig(BaseModel):
    github_token: Optional[str] = Field(None, description="GitHub Personal Access Token")
    devin_api_key: Optional[str] = Field(None, description="Devin AI API Key")
    devin_api_url: str = Field("https://api.devin.ai/v1", description="Devin API Endpoint")
    webhook_url: Optional[str] = Field(None, description="Webhook URL for notifications (Slack/Discord)")
    
    def is_complete(self) -> bool:
        """Returns True only if critical keys are present."""
        return bool(self.github_token and self.devin_api_key)

# --- CRITICAL FIX: Create Alias for compatibility ---
# This allows cli.py to import 'Config' AND devin_client.py to import 'AppConfig'
Config = AppConfig
# ----------------------------------------------------

# 3. Helper Functions
def ensure_config_dir():
    """Creates the configuration directory if it doesn't exist."""
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

def load_config() -> AppConfig:
    """
    Loads config from local file. 
    PRIORITY: Environment Variables > Config File > Defaults.
    """
    ensure_config_dir()
    
    # Start with empty/default config
    config = AppConfig()

    # 1. Try loading from file
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                config = AppConfig(**data)
        except Exception as e:
            logger.warning(f"Failed to parse config file: {e}. Using defaults.")
    
    # 2. Override with Environment Variables (Useful for CI/CD or temporary overrides)
    env_gh = os.getenv("GITHUB_TOKEN")
    env_devin = os.getenv("DEVIN_API_KEY")
    
    if env_gh:
        config.github_token = env_gh
    if env_devin:
        config.devin_api_key = env_devin

    return config

def save_config(config: AppConfig):
    """
    Saves config to local file.
    SAFEGUARD: Creates a backup of the existing config before overwriting.
    """
    ensure_config_dir()
    
    # Feature: Backup existing config
    if CONFIG_FILE.exists():
        try:
            shutil.copy(CONFIG_FILE, BACKUP_FILE)
        except Exception as e:
            logger.warning(f"Could not create backup file: {e}")

    # Write new config
    with open(CONFIG_FILE, "w") as f:
        # Support both Pydantic v1 (json) and v2 (model_dump_json)
        try:
            content = config.model_dump_json(indent=2)
        except AttributeError:
            content = config.json(indent=2)
        f.write(content)
    
    logger.info(f"Configuration saved to {CONFIG_FILE}")

def get_config_or_fail() -> AppConfig:
    """Helper to get config or raise clear error if not set up."""
    config = load_config()
    if not config.is_complete():
        raise ValueError(
            "❌ Configuration missing.\n"
            "Please run 'python main.py setup' OR set GITHUB_TOKEN/DEVIN_API_KEY env vars."
        )
    return config
