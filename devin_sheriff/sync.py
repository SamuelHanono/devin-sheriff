import re
import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from .models import SessionLocal, Repo, Issue
from .github_client import GitHubClient
from .config import load_config

# --- LOGGING SETUP ---
logger = logging.getLogger("sync")
logging.basicConfig(level=logging.INFO)


def extract_pr_number_from_url(pr_url: str) -> Optional[int]:
    """Extract PR number from a GitHub PR URL."""
    if not pr_url:
        return None
    match = re.search(r"/pull/(\d+)", pr_url)
    if match:
        return int(match.group(1))
    return None

def sync_repo_issues(repo_url: str) -> str:
    """
    Full Sync Logic:
    1. Fetches all currently OPEN issues from GitHub.
    2. Adds new issues to DB.
    3. Updates existing issues (title/body).
    4. Marks local issues as DONE if they are no longer in the GitHub list.
    """
    
    # 1. Setup & Configuration Check
    config = load_config()
    if not config.github_token:
        logger.error("Sync failed: No GitHub Token found.")
        return "Error: No GitHub Token found. Run 'setup'."

    # 2. Parse URL
    match = re.search(r"github\.com/([^/]+)/([^/]+)", repo_url)
    if not match:
        return "Error: Invalid Repo URL. Must be 'github.com/owner/repo'."
    
    owner, repo_name = match.groups()
    repo_name = repo_name.replace(".git", "")

    # 3. Fetch Data from GitHub
    logger.info(f"Syncing {owner}/{repo_name}...")
    try:
        gh = GitHubClient(config)
        gh_issues = gh.fetch_open_issues(owner, repo_name)
    except Exception as e:
        logger.error(f"GitHub API Error: {e}")
        return f"GitHub API Error: {e}"

    # 4. Database Operations
    db: Session = SessionLocal()
    try:
        # Find Repo in DB
        repo = db.query(Repo).filter_by(url=repo_url).first()
        if not repo:
            return "Error: Repo not connected locally. Run 'connect' first."

        # Track stats
        open_numbers = set()
        stats = {"new": 0, "updated": 0, "closed": 0, "skipped": 0}

        # --- PROCESS OPEN ISSUES FROM GITHUB ---
        for i_data in gh_issues:
            num = i_data["number"]
            title = i_data["title"]
            body = i_data.get("body", "") or ""
            
            open_numbers.add(num)
            
            # Check if issue exists locally
            issue = db.query(Issue).filter_by(repo_id=repo.id, number=num).first()
            
            if not issue:
                # CREATE NEW
                new_issue = Issue(
                    repo_id=repo.id,
                    number=num,
                    title=title,
                    body=body,
                    state="open",
                    status="NEW"
                )
                db.add(new_issue)
                stats["new"] += 1
                logger.info(f"➕ Added #{num}: {title[:30]}...")
            else:
                # UPDATE EXISTING
                # Check for changes to minimize DB writes
                needs_update = False
                
                # 1. Re-open if it was closed
                if issue.state == "closed":
                    issue.state = "open"
                    if issue.status == "DONE":
                        issue.status = "NEW" # Reset flow if re-opened
                    needs_update = True
                    stats["updated"] += 1
                
                # 2. Update content if changed
                if issue.title != title or issue.body != body:
                    issue.title = title
                    issue.body = body
                    needs_update = True
                    if not needs_update: stats["updated"] += 1 # Avoid double counting
                
                if not needs_update:
                    stats["skipped"] += 1

        # --- CLOSE STALE ISSUES ---
        # Any local issue that is 'open' but NOT in the fetch list is considered closed on GitHub.
        local_open_issues = db.query(Issue).filter(Issue.repo_id == repo.id, Issue.state == "open").all()
        
        for local_issue in local_open_issues:
            if local_issue.number not in open_numbers:
                local_issue.state = "closed"
                local_issue.status = "DONE" # Update UI status
                stats["closed"] += 1
                logger.info(f"✔ Closed #{local_issue.number} (Not found in open list)")

        db.commit()
        
        summary = f"Synced: {stats['new']} new, {stats['updated']} updated, {stats['closed']} closed."
        logger.info(summary)
        return summary

    except Exception as e:
        db.rollback()
        logger.error(f"Database Sync Error: {e}")
        return f"Database Error: {e}"
    finally:
        db.close()


def sync_pr_statuses(repo_url: str) -> Dict[str, Any]:
    """
    Enhanced sync that also checks PR statuses.
    Returns detailed stats about what was updated.
    """
    config = load_config()
    if not config.github_token:
        return {"error": "No GitHub Token found", "stats": {}}

    match = re.search(r"github\.com/([^/]+)/([^/]+)", repo_url)
    if not match:
        return {"error": "Invalid Repo URL", "stats": {}}
    
    owner, repo_name = match.groups()
    repo_name = repo_name.replace(".git", "")

    db: Session = SessionLocal()
    stats = {"issues_updated": 0, "prs_checked": 0, "prs_merged": 0, "prs_closed": 0}
    
    try:
        gh = GitHubClient(config)
        repo = db.query(Repo).filter_by(url=repo_url).first()
        if not repo:
            return {"error": "Repo not connected locally", "stats": stats}

        issues_with_prs = db.query(Issue).filter(
            Issue.repo_id == repo.id,
            Issue.pr_url.isnot(None),
            Issue.status == "PR_OPEN"
        ).all()

        for issue in issues_with_prs:
            pr_number = extract_pr_number_from_url(issue.pr_url)
            if not pr_number:
                continue
            
            stats["prs_checked"] += 1
            pr_data = gh.get_pull_request(owner, repo_name, pr_number)
            
            if pr_data:
                pr_state = pr_data.get("state", "")
                merged = pr_data.get("merged", False)
                
                if merged:
                    issue.status = "DONE"
                    issue.state = "closed"
                    stats["prs_merged"] += 1
                    stats["issues_updated"] += 1
                    logger.info(f"PR #{pr_number} merged - Issue #{issue.number} marked DONE")
                elif pr_state == "closed":
                    stats["prs_closed"] += 1
                    logger.info(f"PR #{pr_number} closed without merge")

        gh_issues = gh.fetch_open_issues(owner, repo_name)
        open_numbers = {i["number"] for i in gh_issues}
        
        local_open_issues = db.query(Issue).filter(
            Issue.repo_id == repo.id,
            Issue.state == "open"
        ).all()
        
        for local_issue in local_open_issues:
            if local_issue.number not in open_numbers:
                local_issue.state = "closed"
                local_issue.status = "DONE"
                stats["issues_updated"] += 1
                logger.info(f"Issue #{local_issue.number} closed on GitHub - marked DONE")

        db.commit()
        return {"error": None, "stats": stats}

    except Exception as e:
        db.rollback()
        logger.error(f"PR Sync Error: {e}")
        return {"error": str(e), "stats": stats}
    finally:
        db.close()
