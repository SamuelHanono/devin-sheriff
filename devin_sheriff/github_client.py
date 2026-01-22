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

    def close_issue(self, owner: str, repo: str, issue_number: int) -> Dict[str, Any]:
        """
        Close an issue on GitHub remotely.
        Returns a dict with 'success' bool and 'error_type' if failed.
        error_type can be: 'permission_denied', 'not_found', 'unknown'
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/issues/{issue_number}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.patch(url, headers=self.headers, json={"state": "closed"})
                resp.raise_for_status()
                logger.info(f"Successfully closed issue #{issue_number} on GitHub")
                return {"success": True, "error_type": None, "message": "Issue closed successfully"}
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                logger.error(f"Permission denied to close issue #{issue_number}")
                return {
                    "success": False, 
                    "error_type": "permission_denied",
                    "message": "Permission Denied. Your GitHub Token is read-only. Please generate a new token with 'repo' scope."
                }
            elif e.response.status_code == 404:
                logger.error(f"Issue #{issue_number} not found")
                return {
                    "success": False,
                    "error_type": "not_found", 
                    "message": f"Issue #{issue_number} not found on GitHub."
                }
            else:
                logger.error(f"Failed to close issue #{issue_number}: {e}")
                return {
                    "success": False,
                    "error_type": "unknown",
                    "message": f"GitHub API error: {e.response.status_code}"
                }
        except Exception as e:
            logger.error(f"Failed to close issue #{issue_number}: {e}")
            return {
                "success": False,
                "error_type": "unknown",
                "message": f"Connection error: {str(e)}"
            }

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

    def get_pr_ci_status(self, owner: str, repo: str, pr_number: int) -> Dict[str, Any]:
        """
        Fetch the combined CI status for a PR's latest commit.
        Returns: {
            'status': 'passing' | 'failing' | 'pending' | 'unknown',
            'total_count': int,
            'failures': [{'name': str, 'description': str}],
            'sha': str (commit SHA)
        }
        """
        try:
            pr = self.get_pull_request(owner, repo, pr_number)
            if not pr:
                return {"status": "unknown", "total_count": 0, "failures": [], "sha": None}
            
            head_sha = pr.get("head", {}).get("sha")
            if not head_sha:
                return {"status": "unknown", "total_count": 0, "failures": [], "sha": None}
            
            with httpx.Client(timeout=self.timeout) as client:
                status_url = f"{self.base_url}/repos/{owner}/{repo}/commits/{head_sha}/status"
                resp = client.get(status_url, headers=self.headers)
                resp.raise_for_status()
                status_data = resp.json()
                
                checks_url = f"{self.base_url}/repos/{owner}/{repo}/commits/{head_sha}/check-runs"
                checks_resp = client.get(checks_url, headers=self.headers)
                checks_resp.raise_for_status()
                checks_data = checks_resp.json()
            
            combined_state = status_data.get("state", "unknown")
            statuses = status_data.get("statuses", [])
            check_runs = checks_data.get("check_runs", [])
            
            failures = []
            pending_count = 0
            
            for s in statuses:
                if s.get("state") == "failure":
                    failures.append({
                        "name": s.get("context", "Unknown"),
                        "description": s.get("description", "No description")
                    })
                elif s.get("state") == "pending":
                    pending_count += 1
            
            for run in check_runs:
                conclusion = run.get("conclusion")
                status = run.get("status")
                if conclusion == "failure" or conclusion == "cancelled":
                    failures.append({
                        "name": run.get("name", "Unknown"),
                        "description": run.get("output", {}).get("summary", "Check failed")
                    })
                elif status == "in_progress" or status == "queued":
                    pending_count += 1
            
            if failures:
                final_status = "failing"
            elif pending_count > 0:
                final_status = "pending"
            elif combined_state == "success" or (not statuses and not check_runs):
                final_status = "passing"
            else:
                final_status = combined_state
            
            total_count = len(statuses) + len(check_runs)
            
            logger.info(f"CI Status for PR #{pr_number}: {final_status} ({total_count} checks)")
            return {
                "status": final_status,
                "total_count": total_count,
                "failures": failures,
                "sha": head_sha
            }
            
        except Exception as e:
            logger.error(f"Failed to fetch CI status for PR #{pr_number}: {e}")
            return {"status": "unknown", "total_count": 0, "failures": [], "sha": None}

    def get_check_run_logs(self, owner: str, repo: str, check_run_id: int) -> Optional[str]:
        """Fetch logs for a specific check run (if available)."""
        url = f"{self.base_url}/repos/{owner}/{repo}/check-runs/{check_run_id}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self.headers)
                resp.raise_for_status()
                data = resp.json()
                output = data.get("output", {})
                return output.get("text") or output.get("summary") or "No logs available"
        except Exception as e:
            logger.error(f"Failed to fetch check run logs: {e}")
            return None

    def create_issue(self, owner: str, repo: str, title: str, body: str) -> Dict[str, Any]:
        """
        Create a new issue on GitHub.
        Returns a dict with 'success', 'issue_number', and 'url' on success,
        or 'success': False and 'error' on failure.
        """
        url = f"{self.base_url}/repos/{owner}/{repo}/issues"
        payload = {
            "title": title,
            "body": body
        }
        
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, headers=self.headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                logger.info(f"Created issue #{data.get('number')}: {title[:50]}...")
                return {
                    "success": True,
                    "issue_number": data.get("number"),
                    "url": data.get("html_url")
                }
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                return {
                    "success": False,
                    "error": "Permission Denied. Your GitHub Token needs 'repo' scope to create issues."
                }
            elif e.response.status_code == 404:
                return {
                    "success": False,
                    "error": f"Repository '{owner}/{repo}' not found."
                }
            else:
                return {
                    "success": False,
                    "error": f"GitHub API error: {e.response.status_code}"
                }
        except Exception as e:
            logger.error(f"Failed to create issue: {e}")
            return {
                "success": False,
                "error": f"Connection error: {str(e)}"
            }

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
