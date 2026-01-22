from .models import SessionLocal, Repo, Issue
from .github_client import GitHubClient
from .config import load_config
import re

def sync_repo_issues(repo_url: str):
    """
    Full Sync:
    1. Fetches all currently OPEN issues from GitHub.
    2. Adds new issues to DB.
    3. Updates existing issues.
    4. Marks local issues as DONE if they are no longer in the GitHub list.
    """
    
    # 1. Setup
    config = load_config()
    if not config.github_token:
        return "Error: No GitHub Token found."

    gh = GitHubClient(config)
    
    # Parse Owner/Repo
    match = re.search(r"github\.com/([^/]+)/([^/]+)", repo_url)
    if not match:
        return "Error: Invalid Repo URL"
    
    owner, repo_name = match.groups()
    repo_name = repo_name.replace(".git", "")

    # 2. Get GitHub Data
    try:
        gh_issues = gh.fetch_open_issues(owner, repo_name)
    except Exception as e:
        return f"GitHub API Error: {e}"

    # 3. Update Database
    db = SessionLocal()
    repo = db.query(Repo).filter_by(url=repo_url).first()
    
    if not repo:
        db.close()
        return "Error: Repo not found in DB. Run connect first."

    # Track which issue numbers are currently open on GitHub
    open_numbers = set()
    new_count = 0
    updated_count = 0
    closed_count = 0

    for i_data in gh_issues:
        num = i_data["number"]
        open_numbers.add(num)
        
        # Check if exists
        issue = db.query(Issue).filter_by(repo_id=repo.id, number=num).first()
        
        if not issue:
            # CREATE NEW
            new_issue = Issue(
                repo_id=repo.id,
                number=num,
                title=i_data["title"],
                body=i_data.get("body", ""),
                state="open",
                status="NEW"
            )
            db.add(new_issue)
            new_count += 1
        else:
            # UPDATE EXISTING
            if issue.state == "closed":
                issue.state = "open" # Re-opened
                # If it was DONE, reset to NEW or keep status if we were working on it
                if issue.status == "DONE":
                     issue.status = "NEW" 
                updated_count += 1
            
            issue.title = i_data["title"]
            issue.body = i_data.get("body", "")

    # 4. Close Stale Issues (The Fix)
    # If a local issue is 'open' but NOT in GitHub's open list, it means it was closed on GitHub.
    local_open_issues = db.query(Issue).filter(Issue.repo_id == repo.id, Issue.state == "open").all()
    
    for local_issue in local_open_issues:
        if local_issue.number not in open_numbers:
            local_issue.state = "closed"
            local_issue.status = "DONE" # <--- THIS UPDATES THE DASHBOARD UI
            closed_count += 1

    db.commit()
    db.close()
    
    return f"Synced: {new_count} new, {updated_count} updated, {closed_count} closed."