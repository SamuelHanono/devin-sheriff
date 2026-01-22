import httpx
import logging
from typing import List, Dict, Any, Optional
from .config import AppConfig

# --- LOGGING SETUP ---
logger = logging.getLogger("github_client")
logging.basicConfig(level=logging.INFO)

class GitHubClient:
    def __init__(self, config: AppConfig):
        if not config.github_token:
             raise ValueError("GitHub Token is missing in configuration.")
             
        self.headers = {
            "Authorization": f"Bearer {config.github_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        self.base_url = "https://api.github.com"
        self.timeout = 10.0 # Seconds

    def verify_auth(self) -> str:
        """Checks if token is valid. Returns username if success."""
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(f"{self.base_url}/user", headers=self.headers)
                response.raise_for_status()
                username = response.json().get("login", "Unknown User")
                logger.info(f"✅ GitHub Authenticated as: {username}")
                return username
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise Exception("GitHub Token is invalid (401 Unauthorized).")
            raise Exception(f"GitHub API Error: {e}")
        except Exception as e:
            logger.error(f"Auth Check Failed: {e}")
            raise Exception(f"Connection Failed: {str(e)}")

    def get_rate_limit(self) -> Dict[str, Any]:
        """Check remaining API calls."""
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{self.base_url}/rate_limit", headers=self.headers)
            resp.raise_for_status()
            data = resp.json().get("rate", {})
            return {
                "limit": data.get("limit"),
                "remaining": data.get("remaining"),
                "reset": data.get("reset")
            }

    def get_repo_details(self, owner: str, repo: str) -> Dict[str, Any]:
        """Get basic repo info like default branch."""
        url = f"{self.base_url}/repos/{owner}/{repo}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self.headers)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise Exception(f"Repository '{owner}/{repo}' not found or private (check permissions).")
            raise e

    def get_single_issue(self, owner: str, repo: str, issue_number: int) -> Optional[Dict[str, Any]]:
        """Fetch a specific issue to refresh its details."""
        url = f"{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self.headers)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch issue #{issue_number}: {e}")
            return None

    def close_issue(self, owner: str, repo: str, issue_number: int) -> bool:
        """Close an issue on GitHub remotely."""
        url = f"{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.patch(url, headers=self.headers, json={"state": "closed"})
                resp.raise_for_status()
                logger.info(f"Successfully closed issue #{issue_number} on GitHub")
                return True
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                logger.error(f"Permission denied to close issue #{issue_number}")
            elif e.response.status_code == 404:
                logger.error(f"Issue #{issue_number} not found")
            else:
                logger.error(f"Failed to close issue #{issue_number}: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to close issue #{issue_number}: {e}")
            return False

    def get_pull_request(self, owner: str, repo: str, pr_number: int) -> Optional[Dict[str, Any]]:
        """Fetch a specific pull request to check its status."""
        url = f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self.headers)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch PR #{pr_number}: {e}")
            return None

    def fetch_open_issues(self, owner: str, repo: str) -> List[Dict[str, Any]]:
        """Fetch all open issues for a repo (handles pagination)."""
        url = f"{self.base_url}/repos/{owner}/{repo}/issues"
        params = {"state": "open", "per_page": 100}
        issues = []
        
        logger.info(f"Fetching issues for {owner}/{repo}...")
        
        try:
            with httpx.Client(timeout=self.timeout) as client:
                while url:
                    resp = client.get(url, headers=self.headers, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                    
                    if not data:
                        break

                    count_this_page = 0
                    for item in data:
                        # Skip Pull Requests (GitHub API returns PRs as issues too)
                        if "pull_request" not in item:
                            issues.append(item)
                            count_this_page += 1
                    
                    # Handle pagination
                    url = resp.links.get("next", {}).get("url")
                    params = {} # params only needed for first page
                    
            logger.info(f"✓ Found {len(issues)} open issues.")
            return issues

        except Exception as e:
            logger.error(f"Error fetching issues: {e}")
            raise e
