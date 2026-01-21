import httpx
from .config import AppConfig

class GitHubClient:
    def __init__(self, config: AppConfig):
        self.headers = {
            "Authorization": f"Bearer {config.github_token}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        self.base_url = "https://api.github.com"

    def verify_auth(self) -> str:
        """Checks if token is valid. Returns username if success."""
        try:
            with httpx.Client() as client:
                response = client.get(f"{self.base_url}/user", headers=self.headers)
                response.raise_for_status()
                return response.json().get("login", "Unknown User")
        except Exception as e:
            raise Exception(f"GitHub Auth Failed: {str(e)}")

    def get_repo_details(self, owner: str, repo: str):
        """Get basic repo info like default branch."""
        url = f"{self.base_url}/repos/{owner}/{repo}"
        with httpx.Client() as client:
            resp = client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    def fetch_open_issues(self, owner: str, repo: str):
        """Fetch all open issues for a repo."""
        url = f"{self.base_url}/repos/{owner}/{repo}/issues"
        params = {"state": "open", "per_page": 100}
        issues = []
        
        with httpx.Client() as client:
            while url:
                resp = client.get(url, headers=self.headers, params=params)
                resp.raise_for_status()
                data = resp.json()
                
                for item in data:
                    # Skip Pull Requests (GitHub API returns PRs as issues too)
                    if "pull_request" not in item:
                        issues.append(item)
                
                # Handle pagination
                url = resp.links.get("next", {}).get("url")
                params = {} # params only needed for first page

        return issues