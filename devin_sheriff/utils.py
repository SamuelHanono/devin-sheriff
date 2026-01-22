import httpx
import logging
from typing import Optional
from .config import load_config

logger = logging.getLogger("utils")
logging.basicConfig(level=logging.INFO)

def send_notification(message: str, level: str = "info") -> bool:
    """
    Send a notification to the configured webhook URL (Slack/Discord).
    
    Args:
        message: The notification message to send
        level: Notification level - 'info', 'success', 'warning', 'error'
    
    Returns:
        True if notification was sent successfully, False otherwise
    """
    config = load_config()
    
    if not config.webhook_url:
        logger.debug("No webhook URL configured, skipping notification")
        return False
    
    emoji_map = {
        "info": "ℹ️",
        "success": "✅",
        "warning": "⚠️",
        "error": "❌"
    }
    emoji = emoji_map.get(level, "📢")
    
    formatted_message = f"{emoji} **Devin Sheriff** | {message}"
    
    payload = {}
    webhook_url = config.webhook_url.lower()
    
    if "discord" in webhook_url:
        payload = {"content": formatted_message}
    elif "slack" in webhook_url or "hooks.slack.com" in webhook_url:
        payload = {"text": formatted_message.replace("**", "*")}
    else:
        payload = {"text": message, "message": message, "content": formatted_message}
    
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(config.webhook_url, json=payload)
            resp.raise_for_status()
            logger.info(f"Notification sent: {message[:50]}...")
            return True
    except httpx.HTTPStatusError as e:
        logger.error(f"Webhook HTTP error: {e.response.status_code}")
        return False
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")
        return False


def notify_scope_complete(issue_number: int, title: str, confidence: int):
    """Send notification when a Scope session completes."""
    message = f"Scope Complete for Issue #{issue_number}: {title[:50]}... (Confidence: {confidence}%)"
    return send_notification(message, level="success")


def notify_pr_opened(issue_number: int, title: str, pr_url: str):
    """Send notification when a PR is opened."""
    message = f"PR Opened for Issue #{issue_number}: {title[:50]}... | {pr_url}"
    return send_notification(message, level="success")


def notify_auto_heal_triggered(issue_number: int, retry_count: int):
    """Send notification when Auto-Healer triggers."""
    message = f"Auto-Healer triggered for Issue #{issue_number} (Retry {retry_count}/3)"
    return send_notification(message, level="warning")


def test_webhook() -> dict:
    """
    Test the webhook configuration by sending a test message.
    Returns a dict with 'success' and 'message' keys.
    """
    config = load_config()
    
    if not config.webhook_url:
        return {
            "success": False,
            "message": "No webhook URL configured. Add one in Settings."
        }
    
    test_message = "Test notification from Devin Sheriff! Your webhook is working."
    success = send_notification(test_message, level="info")
    
    if success:
        return {
            "success": True,
            "message": "Test notification sent successfully!"
        }
    else:
        return {
            "success": False,
            "message": "Failed to send notification. Check your webhook URL."
        }
