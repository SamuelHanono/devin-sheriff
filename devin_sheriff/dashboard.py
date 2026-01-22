import sys
import os
import re
import json
import threading
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List
from difflib import SequenceMatcher
import pandas as pd
import streamlit as st

# --- FIX IMPORT PATHS ---
current_dir = Path(__file__).parent
root_dir = current_dir.parent
sys.path.append(str(root_dir))
# ------------------------

from devin_sheriff.models import SessionLocal, Repo, Issue, reset_database, get_db_path, init_db
from devin_sheriff.devin_client import DevinClient, load_governance_rules, save_governance_rules, RULES_FILE
from devin_sheriff.config import load_config, save_config, CONFIG_DIR
from devin_sheriff.sync import sync_repo_issues, sync_pr_statuses
from devin_sheriff.github_client import GitHubClient
from devin_sheriff.utils import test_webhook, notify_scope_complete, notify_pr_opened

# --- LOGGING SETUP ---
LOG_FILE = CONFIG_DIR / "sheriff.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("dashboard")

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Devin Sheriff v2.0",
    page_icon="🤠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- STYLING ---
st.markdown("""
<style>
    .metric-card {
        background-color: #262730;
        padding: 15px;
        border-radius: 8px;
        border: 1px solid #464b5c;
        text-align: center;
    }
    .status-badge {
        padding: 4px 8px;
        border-radius: 4px;
        font-weight: bold;
        font-size: 0.8em;
    }
    .danger-zone {
        background-color: #3d1f1f;
        border: 1px solid #8b0000;
        border-radius: 8px;
        padding: 10px;
        margin-top: 20px;
    }
    .log-viewer {
        font-family: monospace;
        font-size: 12px;
        background-color: #1e1e1e;
        padding: 10px;
        border-radius: 5px;
        max-height: 400px;
        overflow-y: auto;
    }
</style>
""", unsafe_allow_html=True)

# --- SESSION STATE INITIALIZATION ---
def init_session_state():
    """Initialize session state variables for caching and async operations."""
    if 'repos_cache' not in st.session_state:
        st.session_state.repos_cache = None
    if 'repos_cache_time' not in st.session_state:
        st.session_state.repos_cache_time = None
    if 'issues_cache' not in st.session_state:
        st.session_state.issues_cache = {}
    if 'async_task' not in st.session_state:
        st.session_state.async_task = None
    if 'async_status' not in st.session_state:
        st.session_state.async_status = None
    if 'async_result' not in st.session_state:
        st.session_state.async_result = None
    if 'edited_plan' not in st.session_state:
        st.session_state.edited_plan = None
    if 'show_plan_editor' not in st.session_state:
        st.session_state.show_plan_editor = False

init_session_state()

# --- DATABASE HELPER ---
def get_db():
    return SessionLocal()

# --- CACHING HELPERS ---
CACHE_TTL = 30  # seconds

def get_cached_repos(force_refresh=False):
    """Get repos with caching to reduce DB queries."""
    now = time.time()
    if (not force_refresh and 
        st.session_state.repos_cache is not None and 
        st.session_state.repos_cache_time is not None and
        now - st.session_state.repos_cache_time < CACHE_TTL):
        return st.session_state.repos_cache
    
    db = get_db()
    try:
        repos = db.query(Repo).all()
        st.session_state.repos_cache = repos
        st.session_state.repos_cache_time = now
        return repos
    finally:
        db.close()

def invalidate_cache():
    """Clear all caches to force fresh data."""
    st.session_state.repos_cache = None
    st.session_state.repos_cache_time = None
    st.session_state.issues_cache = {}

# --- ASYNC TASK HELPERS ---
class AsyncTaskRunner:
    """Helper class to run Devin tasks in background threads."""
    
    def __init__(self):
        self.thread = None
        self.status = "idle"
        self.result = None
        self.error = None
        self.progress = 0
        self.task_type = None
    
    def run_scope(self, repo_url: str, issue_number: int, title: str, body: str, 
                  repo_id: int = None):
        """Run scoping in background with Archive context."""
        self.status = "running"
        self.progress = 0
        self.error = None
        self.task_type = "scope"
        
        def task():
            try:
                self.progress = 10
                cfg = load_config()
                client = DevinClient(cfg)
                
                self.progress = 20
                similar_context = None
                if repo_id:
                    similar_issues = find_similar_closed_issues(title, repo_id)
                    if similar_issues:
                        similar_context = build_archive_context(similar_issues)
                        logger.info(f"Archive: Found {len(similar_issues)} similar closed issues")
                
                self.progress = 30
                plan = client.start_scope_session(
                    repo_url, issue_number, title, body,
                    similar_issues_context=similar_context
                )
                
                self.progress = 100
                self.result = plan
                self.status = "completed"
            except Exception as e:
                self.error = str(e)
                self.status = "failed"
        
        self.thread = threading.Thread(target=task, daemon=True)
        self.thread.start()
    
    def run_execute(self, repo_url: str, issue_number: int, title: str, plan_json: dict,
                    ci_failure_context: Optional[str] = None):
        """Run execution in background. Optionally accepts ci_failure_context for Auto-Healer."""
        self.status = "running"
        self.progress = 0
        self.error = None
        self.task_type = "execute"
        
        def task():
            try:
                self.progress = 10
                cfg = load_config()
                client = DevinClient(cfg)
                
                self.progress = 30
                result = client.start_execute_session(
                    repo_url, issue_number, title, plan_json,
                    ci_failure_context=ci_failure_context
                )
                
                self.progress = 100
                self.result = result
                self.status = "completed"
            except Exception as e:
                self.error = str(e)
                self.status = "failed"
        
        self.thread = threading.Thread(target=task, daemon=True)
        self.thread.start()
    
    def is_running(self):
        return self.status == "running"
    
    def get_progress(self):
        return self.progress

# Global task runner
if 'task_runner' not in st.session_state:
    st.session_state.task_runner = AsyncTaskRunner()

# --- RISK ANALYSIS HELPER ---
def analyze_risk_level(files_to_change: list) -> tuple:
    """
    Analyze the risk level based on files being changed.
    Returns (risk_level, risk_color, risk_description)
    """
    if not files_to_change:
        return ("UNKNOWN", "gray", "No files specified in plan")
    
    high_risk_patterns = [
        'config.py', '.env', 'secrets', 'credentials', 'auth', 
        'password', 'token', 'key', 'private', 'secret',
        'settings.py', 'config.json', 'config.yaml', 'config.yml',
        '.pem', '.key', 'oauth', 'jwt'
    ]
    
    medium_risk_patterns = [
        'main.py', 'models.py', 'app.py', 'database', 'db.py',
        'core/', 'src/main', 'index.py', 'server.py', 'api.py',
        'routes.py', 'views.py', 'schema', 'migration'
    ]
    
    low_risk_patterns = [
        'readme', 'test', '.txt', '.md', 'docs/', 'doc/',
        'example', 'sample', '.rst', 'changelog', 'license',
        'contributing', '.gitignore', 'requirements.txt'
    ]
    
    files_lower = [f.lower() for f in files_to_change]
    
    for file in files_lower:
        for pattern in high_risk_patterns:
            if pattern in file:
                return ("HIGH", "red", f"Touches sensitive file: {file}")
    
    for file in files_lower:
        for pattern in medium_risk_patterns:
            if pattern in file:
                return ("MEDIUM", "orange", f"Modifies core logic: {file}")
    
    for file in files_lower:
        for pattern in low_risk_patterns:
            if pattern in file:
                return ("LOW", "green", "Only touches docs/tests")
    
    return ("MEDIUM", "orange", "Standard code changes")


# --- LOG VIEWER HELPER ---
def read_log_file(num_lines: int = 50) -> str:
    """Read the last N lines from the log file."""
    try:
        if not LOG_FILE.exists():
            return "No log file found. Logs will appear here after operations."
        
        with open(LOG_FILE, 'r') as f:
            lines = f.readlines()
            return ''.join(lines[-num_lines:])
    except Exception as e:
        return f"Error reading log file: {e}"


# --- THE ARCHIVE: SIMILAR ISSUES HELPER ---
def find_similar_closed_issues(current_title: str, repo_id: int, top_n: int = 2) -> List[Dict[str, Any]]:
    """
    Find similar closed issues from The Archive.
    Uses simple keyword matching with SequenceMatcher for similarity.
    Returns top N matches with their details.
    """
    db = get_db()
    try:
        closed_issues = db.query(Issue).filter(
            Issue.repo_id == repo_id,
            Issue.state == "closed",
            Issue.status == "DONE"
        ).all()
        
        if not closed_issues:
            return []
        
        similarities = []
        for issue in closed_issues:
            ratio = SequenceMatcher(None, current_title.lower(), issue.title.lower()).ratio()
            if ratio > 0.3:
                files_changed = []
                if issue.scope_json and "files_to_change" in issue.scope_json:
                    files_changed = issue.scope_json.get("files_to_change", [])
                
                similarities.append({
                    "number": issue.number,
                    "title": issue.title,
                    "similarity": ratio,
                    "files_changed": files_changed,
                    "summary": issue.scope_json.get("summary", "") if issue.scope_json else ""
                })
        
        similarities.sort(key=lambda x: x["similarity"], reverse=True)
        return similarities[:top_n]
    finally:
        db.close()


def build_archive_context(similar_issues: List[Dict[str, Any]]) -> str:
    """Build context string from similar issues for Devin prompt injection."""
    if not similar_issues:
        return ""
    
    context_parts = ["We have solved similar issues before:"]
    for issue in similar_issues:
        files_str = ", ".join(issue["files_changed"][:5]) if issue["files_changed"] else "N/A"
        context_parts.append(
            f"\nIssue #{issue['number']}: {issue['title']}\n"
            f"  Summary: {issue['summary'][:200]}...\n"
            f"  Files Changed: {files_str}\n"
            f"  Use this approach if applicable."
        )
    return "\n".join(context_parts)


# --- AUTO-HEALER: CI STATUS HELPERS ---
def check_and_update_ci_status(issue: Issue, repo: Repo, db) -> Dict[str, Any]:
    """
    Check CI status for an issue with PR_OPEN status.
    Updates the issue's ci_status field and returns status info.
    """
    if issue.status != "PR_OPEN" or not issue.pr_url:
        return {"status": "not_applicable"}
    
    try:
        import re
        pr_match = re.search(r"/pull/(\d+)", issue.pr_url)
        if not pr_match:
            return {"status": "unknown", "error": "Could not parse PR number"}
        
        pr_number = int(pr_match.group(1))
        
        cfg = load_config()
        gh = GitHubClient(cfg)
        ci_result = gh.get_pr_ci_status(repo.owner, repo.name, pr_number)
        
        issue.ci_status = ci_result["status"]
        db.commit()
        
        return ci_result
    except Exception as e:
        logger.error(f"Failed to check CI status: {e}")
        return {"status": "unknown", "error": str(e)}


def trigger_auto_heal(issue: Issue, repo: Repo, ci_failures: List[Dict], db):
    """
    Trigger Auto-Healer: Start a new execute session to fix CI failures.
    Truncates failure descriptions to avoid token limits.
    """
    if issue.retry_count >= 3:
        logger.warning(f"Issue #{issue.number} has reached max retries (3)")
        return {"error": "Max retries reached"}
    
    failure_context = "CI Check Failures:\n"
    for f in ci_failures[:5]:
        name = f.get('name', 'Unknown')[:100]
        desc = f.get('description', 'Check failed')[:200]
        failure_context += f"- {name}: {desc}\n"
    
    if len(failure_context) > 1500:
        failure_context = failure_context[:1500] + "\n... (truncated for token safety)"
    
    issue.retry_count = (issue.retry_count or 0) + 1
    db.commit()
    
    logger.info(f"Auto-Healer triggered for Issue #{issue.number} (Retry {issue.retry_count}/3)")
    
    return {
        "triggered": True,
        "retry_count": issue.retry_count,
        "failure_context": failure_context
    }


def get_ci_badge(ci_status: str, retry_count: int = 0) -> str:
    """Return CI status badge HTML."""
    if ci_status == "passing":
        return "🟢 Passing"
    elif ci_status == "failing":
        if retry_count >= 3:
            return "⚫ Failed (Max Retries)"
        return f"🔴 Failing (Retry {retry_count}/3)"
    elif ci_status == "pending":
        return "🟡 Pending"
    else:
        return "⚪ Unknown"


# --- THE WANTED LIST: TECH DEBT SCANNER ---
def scan_for_todos(repo_path: str) -> List[Dict[str, Any]]:
    """
    Scan a local repository for TODO and FIXME comments.
    Returns a list of dicts with file, line, and comment info.
    """
    todos = []
    patterns = [
        r'#\s*(TODO|FIXME|XXX|HACK|BUG)[\s:]+(.+)',
        r'//\s*(TODO|FIXME|XXX|HACK|BUG)[\s:]+(.+)',
        r'/\*\s*(TODO|FIXME|XXX|HACK|BUG)[\s:]+(.+)',
    ]
    
    skip_dirs = {'.git', 'node_modules', '__pycache__', 'venv', '.venv', 'dist', 'build'}
    skip_extensions = {'.pyc', '.pyo', '.so', '.dll', '.exe', '.bin', '.jpg', '.png', '.gif'}
    
    repo_path = Path(repo_path)
    if not repo_path.exists():
        return []
    
    for file_path in repo_path.rglob('*'):
        if file_path.is_dir():
            continue
        
        if any(skip_dir in file_path.parts for skip_dir in skip_dirs):
            continue
        
        if file_path.suffix.lower() in skip_extensions:
            continue
        
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line_num, line in enumerate(f, 1):
                    for pattern in patterns:
                        match = re.search(pattern, line, re.IGNORECASE)
                        if match:
                            tag = match.group(1).upper()
                            comment = match.group(2).strip()[:100]
                            todos.append({
                                "file": str(file_path.relative_to(repo_path)),
                                "line": line_num,
                                "tag": tag,
                                "comment": comment,
                                "full_path": str(file_path)
                            })
                            break
        except Exception:
            continue
    
    return todos


def get_tribunal_grade_color(grade: str) -> str:
    """Return color for tribunal grade."""
    grade_colors = {
        "A": "green",
        "B": "blue",
        "C": "orange",
        "D": "red",
        "F": "red"
    }
    return grade_colors.get(grade.upper(), "gray")


# --- MISSION LOG HELPER ---
def get_mission_log_entries(num_entries: int = 20) -> List[Dict[str, Any]]:
    """Get recent log entries formatted for the Mission Log display."""
    entries = []
    try:
        if not LOG_FILE.exists():
            return [{"time": datetime.now().strftime("%H:%M:%S"), "level": "INFO", "message": "Mission Log initialized. Waiting for activity..."}]
        
        with open(LOG_FILE, 'r') as f:
            lines = f.readlines()[-num_entries:]
            for line in lines:
                parts = line.strip().split(' - ', 3)
                if len(parts) >= 4:
                    timestamp = parts[0].split(' ')[-1] if ' ' in parts[0] else parts[0]
                    level = parts[2] if len(parts) > 2 else "INFO"
                    message = parts[3] if len(parts) > 3 else line.strip()
                    entries.append({
                        "time": timestamp[:8],
                        "level": level,
                        "message": message[:100]
                    })
                else:
                    entries.append({
                        "time": datetime.now().strftime("%H:%M:%S"),
                        "level": "INFO",
                        "message": line.strip()[:100]
                    })
    except Exception as e:
        entries.append({"time": datetime.now().strftime("%H:%M:%S"), "level": "ERROR", "message": f"Log read error: {e}"})
    
    return entries if entries else [{"time": datetime.now().strftime("%H:%M:%S"), "level": "INFO", "message": "No activity yet."}]


# --- FIRST-RUN WIZARD ---
def render_first_run_wizard():
    """Render a friendly welcome screen for first-time users."""
    st.markdown("""
    <style>
        .welcome-container {
            text-align: center;
            padding: 40px 20px;
        }
        .welcome-title {
            font-size: 3em;
            margin-bottom: 10px;
        }
        .welcome-subtitle {
            font-size: 1.2em;
            color: #888;
            margin-bottom: 30px;
        }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="welcome-container">', unsafe_allow_html=True)
    st.markdown("# 🤠 Welcome to Devin Sheriff!")
    st.markdown("### Your AI-Powered Issue Management Partner")
    st.markdown("</div>", unsafe_allow_html=True)
    
    st.markdown("---")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.markdown("#### Connect Your First Repository")
        st.markdown("Paste a GitHub repository URL below to get started. Devin Sheriff works with **any** public or private repository you have access to.")
        
        repo_url = st.text_input(
            "GitHub Repository URL",
            placeholder="https://github.com/your-username/your-repo",
            help="Enter the full URL of your GitHub repository"
        )
        
        if st.button("🚀 Connect Repository", type="primary", use_container_width=True):
            if not repo_url:
                st.error("Please enter a repository URL.")
            elif "github.com" not in repo_url:
                st.error("Please enter a valid GitHub URL (must contain 'github.com').")
            else:
                with st.spinner("Connecting to repository..."):
                    result = connect_repo_from_dashboard(repo_url)
                    if result["success"]:
                        st.success(f"Successfully connected to **{result['repo_name']}**!")
                        st.balloons()
                        invalidate_cache()
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error(result["error"])
        
        st.markdown("---")
        
        st.markdown("#### Quick Setup Checklist")
        config = load_config()
        
        gh_status = "configured" if config.github_token else "missing"
        devin_status = "configured" if config.devin_api_key else "missing"
        
        if config.github_token:
            st.markdown("- [x] GitHub Token configured")
        else:
            st.markdown("- [ ] GitHub Token **not configured**")
            st.caption("Run `python main.py setup` in your terminal to configure.")
        
        if config.devin_api_key:
            st.markdown("- [x] Devin API Key configured")
        else:
            st.markdown("- [ ] Devin API Key **not configured**")
            st.caption("Run `python main.py setup` in your terminal to configure.")
        
        if not config.is_complete():
            st.warning("Please complete the setup before connecting a repository.")
            st.code("python main.py setup", language="bash")
    
    render_danger_zone()


def connect_repo_from_dashboard(repo_url: str) -> Dict[str, Any]:
    """Connect a repository directly from the dashboard."""
    import re
    
    config = load_config()
    if not config.github_token:
        return {"success": False, "error": "GitHub Token not configured. Run 'python main.py setup' first."}
    
    match = re.search(r"github\.com/([^/]+)/([^/]+)", repo_url)
    if not match:
        return {"success": False, "error": "Invalid GitHub URL. Must be in format: github.com/owner/repo"}
    
    owner, repo_name = match.groups()
    repo_name = repo_name.replace(".git", "").rstrip("/")
    
    try:
        gh = GitHubClient(config)
        gh.get_repo_details(owner, repo_name)
    except Exception as e:
        error_msg = str(e)
        if "not found" in error_msg.lower() or "404" in error_msg:
            return {"success": False, "error": f"Repository '{owner}/{repo_name}' not found. Check the URL and ensure you have access."}
        elif "401" in error_msg or "unauthorized" in error_msg.lower():
            return {"success": False, "error": "GitHub authentication failed. Your token may be invalid or expired."}
        else:
            return {"success": False, "error": f"Could not connect to repository: {error_msg}"}
    
    db = get_db()
    try:
        existing = db.query(Repo).filter(Repo.owner == owner, Repo.name == repo_name).first()
        if existing:
            return {"success": True, "repo_name": f"{owner}/{repo_name}", "message": "Repository already connected."}
        
        clean_url = f"https://github.com/{owner}/{repo_name}"
        repo = Repo(url=clean_url, owner=owner, name=repo_name)
        db.add(repo)
        db.commit()
        
        try:
            sync_repo_issues(clean_url)
        except Exception as sync_error:
            logger.warning(f"Initial sync failed: {sync_error}")
        
        return {"success": True, "repo_name": f"{owner}/{repo_name}"}
    except Exception as e:
        db.rollback()
        return {"success": False, "error": f"Database error: {str(e)}"}
    finally:
        db.close()


# --- MAIN DASHBOARD LOGIC ---
def main():
    st.title("🤠 Devin Sheriff v2.0")

    # 1. SIDEBAR: REPOSITORY SELECTION
    st.sidebar.header("📂 Repository")
    repos = get_cached_repos()
    
    # FEATURE A: First-Run Wizard
    if not repos:
        render_first_run_wizard()
        return

    repo_names = [r.name for r in repos]
    selected_repo_name = st.sidebar.selectbox("Select Repository", repo_names)
    selected_repo = next(r for r in repos if r.name == selected_repo_name)

    # AUTO-SYNC: Automatically sync on first load for each repo
    auto_sync_key = f"auto_synced_{selected_repo.id}"
    if auto_sync_key not in st.session_state:
        st.session_state[auto_sync_key] = False
    
    if not st.session_state[auto_sync_key]:
        with st.spinner(f"Auto-syncing {selected_repo.name}..."):
            try:
                sync_repo_issues(selected_repo.url)
                sync_pr_statuses(selected_repo.url)
                invalidate_cache()
                st.session_state[auto_sync_key] = True
            except Exception as e:
                st.sidebar.warning(f"Auto-sync failed: {e}")
                st.session_state[auto_sync_key] = True

    # 2. SIDEBAR: GLOBAL REFRESH BUTTONS (Always Visible)
    st.sidebar.markdown("---")
    st.sidebar.subheader("🔄 Sync Controls")
    
    col1, col2 = st.sidebar.columns(2)
    
    with col1:
        if st.button("🔄 Quick Sync", use_container_width=True, help="Sync issues from GitHub"):
            with st.spinner(f"Syncing {selected_repo.name}..."):
                try:
                    msg = sync_repo_issues(selected_repo.url)
                    invalidate_cache()
                    st.success(msg)
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Sync failed: {e}")
    
    with col2:
        if st.button("🔍 Deep Sync", use_container_width=True, help="Sync + check PR statuses"):
            with st.spinner("Deep syncing..."):
                try:
                    sync_repo_issues(selected_repo.url)
                    result = sync_pr_statuses(selected_repo.url)
                    invalidate_cache()
                    
                    if result.get("error"):
                        st.error(result["error"])
                    else:
                        stats = result["stats"]
                        st.success(f"PRs checked: {stats['prs_checked']}, Merged: {stats['prs_merged']}, Issues updated: {stats['issues_updated']}")
                    time.sleep(1.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"Deep sync failed: {e}")

    # View Filter
    st.sidebar.markdown("---")
    filter_status = st.sidebar.radio(
        "Filter View",
        ["All Open Issues", "New / Untouched", "Scoped (Ready to Fix)", "PR Open"],
        index=0
    )

    # Settings & Security Section
    render_settings_security()
    
    # Render Danger Zone in sidebar
    render_danger_zone()
    
    # 3. MAIN CONTENT AREA WITH TABS
    tab_main, tab_laws, tab_log = st.tabs(["🛠 Mission Control", "👮‍♂️ Sheriff's Rules", "📡 Live Mission Log"])
    
    with tab_main:
        render_mission_control(selected_repo, filter_status)
    
    with tab_laws:
        render_laws_tab()
    
    with tab_log:
        render_live_mission_log()


# --- FEATURE C: LIVE MISSION LOG ---
def render_live_mission_log():
    """Render the Live Mission Log - a real-time scrolling terminal view of system activity."""
    st.subheader("📡 Live Mission Log")
    st.caption("Real-time view of Sheriff activity. Auto-refreshes every 5 seconds when enabled.")
    
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        auto_refresh = st.checkbox("Auto-refresh", value=False, key="mission_log_auto_refresh")
    
    with col2:
        num_lines = st.selectbox("Lines to show", [20, 50, 100, 200], index=1)
    
    with col3:
        if st.button("🔄 Refresh Now", use_container_width=True):
            st.rerun()
    
    st.markdown("---")
    
    log_entries = get_mission_log_entries(num_lines)
    
    log_html = """
    <style>
        .mission-log {
            font-family: 'Courier New', monospace;
            font-size: 12px;
            background-color: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            padding: 15px;
            max-height: 500px;
            overflow-y: auto;
            color: #c9d1d9;
        }
        .log-entry {
            margin: 2px 0;
            padding: 2px 5px;
            border-radius: 3px;
        }
        .log-time {
            color: #8b949e;
            margin-right: 10px;
        }
        .log-level-INFO { color: #58a6ff; }
        .log-level-WARNING { color: #d29922; }
        .log-level-ERROR { color: #f85149; }
        .log-level-DEBUG { color: #8b949e; }
        .log-message { color: #c9d1d9; }
    </style>
    <div class="mission-log">
    """
    
    for entry in log_entries:
        level_class = f"log-level-{entry['level']}"
        log_html += f"""
        <div class="log-entry">
            <span class="log-time">[{entry['time']}]</span>
            <span class="{level_class}">[{entry['level']}]</span>
            <span class="log-message">{entry['message']}</span>
        </div>
        """
    
    log_html += "</div>"
    
    st.markdown(log_html, unsafe_allow_html=True)
    
    if auto_refresh:
        time.sleep(5)
        st.rerun()
    
    st.markdown("---")
    st.markdown("**Log File Location:**")
    st.code(str(LOG_FILE), language=None)
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📥 Download Full Log", use_container_width=True):
            try:
                if LOG_FILE.exists():
                    log_content = LOG_FILE.read_text()
                    st.download_button(
                        label="Click to Download",
                        data=log_content,
                        file_name="sheriff.log",
                        mime="text/plain"
                    )
                else:
                    st.warning("No log file found yet.")
            except Exception as e:
                st.error(f"Error reading log: {e}")
    
    with col2:
        if st.button("🗑️ Clear Log File", use_container_width=True):
            try:
                if LOG_FILE.exists():
                    LOG_FILE.write_text("")
                    st.success("Log file cleared!")
                    time.sleep(1)
                    st.rerun()
            except Exception as e:
                st.error(f"Error clearing log: {e}")


# --- MISSION CONTROL: 3-COLUMN LAYOUT ---
def render_mission_control(selected_repo, filter_status):
    """Render the Mission Control dashboard with 3-column layout for high data density."""
    db = get_db()
    
    try:
        all_issues = db.query(Issue).filter(
            Issue.repo_id == selected_repo.id,
            Issue.state == "open"
        ).all()
        
        count_new = len([i for i in all_issues if i.status == "NEW"])
        count_scoped = len([i for i in all_issues if i.status == "SCOPED"])
        count_pr = len([i for i in all_issues if i.status == "PR_OPEN"])
        count_executing = len([i for i in all_issues if i.status == "EXECUTING"])

        st.markdown("### 📊 Status Overview")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("🆕 New", count_new)
        m2.metric("📋 Scoped", count_scoped)
        m3.metric("⚙️ Executing", count_executing)
        m4.metric("🚀 PRs Open", count_pr)

        st.divider()

        if filter_status == "New / Untouched":
            display_issues = [i for i in all_issues if i.status == "NEW"]
        elif filter_status == "Scoped (Ready to Fix)":
            display_issues = [i for i in all_issues if i.status == "SCOPED"]
        elif filter_status == "PR Open":
            display_issues = [i for i in all_issues if i.status == "PR_OPEN"]
        else:
            display_issues = all_issues

        if not display_issues:
            st.info(f"No issues found matching filter: **{filter_status}**")
            st.markdown("---")
            st.markdown("#### Quick Actions")
            if st.button("🔄 Sync Issues from GitHub", use_container_width=True):
                with st.spinner("Syncing..."):
                    msg = sync_repo_issues(selected_repo.url)
                    invalidate_cache()
                    st.success(msg)
                    time.sleep(1)
                    st.rerun()
            return

        col_list, col_detail, col_actions = st.columns([1, 2, 1])
        
        with col_list:
            st.markdown("#### 📋 Issue Queue")
            
            issue_options = {f"#{i.number}: {i.title[:30]}..." if len(i.title) > 30 else f"#{i.number}: {i.title}": i.number for i in display_issues}
            
            for label, num in issue_options.items():
                issue = next((i for i in display_issues if i.number == num), None)
                if issue:
                    status_emoji = {
                        "NEW": "🆕",
                        "SCOPED": "📋",
                        "EXECUTING": "⚙️",
                        "PR_OPEN": "🚀",
                        "DONE": "✅",
                        "FAILED": "❌"
                    }.get(issue.status, "❓")
                    
                    is_selected = st.session_state.get('selected_issue_number') == num
                    button_type = "primary" if is_selected else "secondary"
                    
                    if st.button(f"{status_emoji} #{issue.number}", key=f"issue_btn_{num}", use_container_width=True, type=button_type):
                        st.session_state.selected_issue_number = num
                        st.rerun()
            
            if 'selected_issue_number' not in st.session_state and display_issues:
                st.session_state.selected_issue_number = display_issues[0].number
        
        selected_number = st.session_state.get('selected_issue_number')
        current_issue = None
        if selected_number:
            current_issue = db.query(Issue).filter(
                Issue.repo_id == selected_repo.id, 
                Issue.number == selected_number
            ).first()
        
        with col_detail:
            if current_issue:
                render_issue_detail_panel(current_issue, selected_repo, db)
            else:
                st.info("Select an issue from the queue to view details.")
        
        with col_actions:
            if current_issue:
                render_action_panel(current_issue, selected_repo, db)
            else:
                st.markdown("#### ⚡ Actions")
                st.caption("Select an issue to see available actions.")
                
    finally:
        db.close()


def render_issue_detail_panel(issue, repo, db):
    """Render the central detail panel for an issue."""
    st.markdown(f"#### Issue #{issue.number}")
    st.markdown(f"**{issue.title}**")
    
    status_colors = {
        "NEW": "gray",
        "SCOPED": "orange",
        "EXECUTING": "blue",
        "PR_OPEN": "green",
        "DONE": "green",
        "FAILED": "red"
    }
    status_color = status_colors.get(issue.status, "gray")
    st.markdown(f"Status: :{status_color}[**{issue.status}**]")
    
    with st.expander("📖 Description", expanded=False):
        st.markdown(issue.body if issue.body else "*No description provided.*")
    
    if issue.scope_json:
        if "error" in issue.scope_json:
            st.error(f"**Scoping Failed:** {issue.scope_json.get('error', 'Unknown error')}")
        else:
            st.success(f"**Plan Ready** (Confidence: {issue.confidence}%)")
            
            files_to_change = issue.scope_json.get("files_to_change", [])
            risk_level, risk_color, risk_desc = analyze_risk_level(files_to_change)
            risk_emoji = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟢", "UNKNOWN": "⚪"}.get(risk_level, "⚪")
            st.markdown(f"**Risk:** {risk_emoji} :{risk_color}[{risk_level}] - {risk_desc}")
            
            with st.expander("📋 Action Plan", expanded=True):
                st.markdown("**Strategy:**")
                for step in issue.scope_json.get("action_plan", []):
                    st.markdown(f"- {step}")
                
                st.markdown("**Files to Change:**")
                for f in files_to_change:
                    st.code(f, language="bash")
    
    if issue.pr_url:
        st.markdown("---")
        st.success(f"🚀 [View Pull Request]({issue.pr_url})")
        
        ci_badge = get_ci_badge(issue.ci_status or "unknown", issue.retry_count or 0)
        st.markdown(f"**CI Status:** {ci_badge}")


def render_action_panel(issue, repo, db):
    """Render the action panel for an issue."""
    st.markdown("#### ⚡ Actions")
    
    task_runner = st.session_state.task_runner
    
    if task_runner.status == "completed":
        handle_task_completion(issue, task_runner, db)
        st.rerun()
    elif task_runner.status == "failed":
        st.error(f"Task failed: {task_runner.error}")
        task_runner.status = "idle"
    
    if task_runner.is_running():
        st.info("🔄 Task in progress...")
        progress = task_runner.get_progress()
        st.progress(progress / 100)
        time.sleep(2)
        st.rerun()
    else:
        if issue.status in ["NEW", "DONE"]:
            if st.button("🔍 Scope Issue", key=f"scope_{issue.id}", type="primary", use_container_width=True):
                task_runner.run_scope(repo.url, issue.number, issue.title, issue.body, repo_id=repo.id)
                st.info("Scoping started...")
                time.sleep(1)
                st.rerun()
        
        if issue.status == "SCOPED":
            if st.button("🛠 Execute Fix", key=f"exec_{issue.id}", type="primary", use_container_width=True):
                plan_to_use = st.session_state.get('edited_plan') or issue.scope_json
                task_runner.run_execute(repo.url, issue.number, issue.title, plan_to_use)
                st.info("Execution started...")
                time.sleep(1)
                st.rerun()
            
            if st.button("🔄 Re-Scope", key=f"rescope_{issue.id}", use_container_width=True):
                issue.status = "NEW"
                issue.scope_json = None
                issue.confidence = 0
                st.session_state.edited_plan = None
                db.commit()
                invalidate_cache()
                st.rerun()
        
        if issue.status == "PR_OPEN" and issue.pr_url:
            if st.button("🔄 Check CI", key=f"check_ci_{issue.id}", use_container_width=True):
                with st.spinner("Checking CI..."):
                    ci_result = check_and_update_ci_status(issue, repo, db)
                    if ci_result["status"] == "failing" and (issue.retry_count or 0) < 3:
                        if st.button("🔧 Auto-Heal", key=f"heal_{issue.id}", type="primary"):
                            heal_result = trigger_auto_heal(issue, repo, ci_result.get("failures", []), db)
                            if "error" not in heal_result:
                                plan_to_use = issue.scope_json or {}
                                task_runner.run_execute(
                                    repo.url, issue.number, issue.title, plan_to_use,
                                    ci_failure_context=heal_result.get("failure_context")
                                )
                                st.rerun()
                    st.rerun()
    
    st.markdown("---")
    st.markdown("**Manual Controls**")
    
    if issue.status != "DONE":
        if st.button("✅ Close Issue", key=f"close_{issue.id}", use_container_width=True):
            result = close_issue_workflow(issue, repo, db, close_on_github=False)
            st.success(f"Issue #{issue.number} closed!")
            invalidate_cache()
            time.sleep(1)
            st.rerun()
    
    if st.button("🗑 Reset State", key=f"reset_{issue.id}", use_container_width=True):
        issue.status = "NEW"
        issue.scope_json = None
        issue.confidence = None
        issue.pr_url = None
        st.session_state.edited_plan = None
        db.commit()
        invalidate_cache()
        st.rerun()


def render_main_dashboard(selected_repo, filter_status):
    """Legacy function - redirects to Mission Control."""
    render_mission_control(selected_repo, filter_status)


def render_settings_security():
    """Render the Settings & Security section in sidebar."""
    st.sidebar.markdown("---")
    
    with st.sidebar.expander("⚙️ Settings & Security", expanded=False):
        st.markdown("**Local Storage Paths**")
        st.caption("Your data is stored locally on your machine:")
        
        config_path = CONFIG_DIR / "config.json"
        db_path = get_db_path()
        
        st.code(f"Config: {config_path}", language=None)
        st.code(f"Database: {db_path}", language=None)
        st.code(f"Logs: {LOG_FILE}", language=None)
        
        st.markdown("---")
        st.markdown("**The Telegraph (Webhooks)**")
        st.caption("Configure a webhook URL to receive notifications (Slack/Discord).")
        
        config = load_config()
        current_webhook = config.webhook_url or ""
        
        new_webhook = st.text_input("Webhook URL", value=current_webhook, placeholder="https://hooks.slack.com/...")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 Save Webhook", use_container_width=True):
                config.webhook_url = new_webhook if new_webhook else None
                save_config(config)
                st.success("Webhook saved!")
                time.sleep(1)
                st.rerun()
        
        with col2:
            if st.button("🔔 Test Notification", use_container_width=True):
                result = test_webhook()
                if result["success"]:
                    st.success(result["message"])
                else:
                    st.error(result["message"])
        
        st.markdown("---")
        st.markdown("**Security Note**")
        st.caption("Your API keys (GitHub PAT and Devin API Key) are stored locally in the config file above. They are never sent to any third-party servers except GitHub and Devin APIs.")
        
        if config_path.exists():
            st.success("Config file exists")
        else:
            st.warning("Config file not found. Run setup first.")


def render_danger_zone():
    """Render the Danger Zone section in sidebar."""
    st.sidebar.markdown("---")
    st.sidebar.markdown("### ⚠️ Danger Zone")
    
    with st.sidebar.expander("🧨 Nuclear Options", expanded=False):
        st.warning("These actions are irreversible!")
        
        if st.button("🧨 Delete All Data & Reset", type="primary", use_container_width=True):
            st.session_state.show_reset_confirm = True
        
        if st.session_state.get('show_reset_confirm', False):
            st.error("Are you absolutely sure? This will delete ALL data!")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Yes, Reset Everything", type="primary"):
                    with st.spinner("Resetting database..."):
                        success = reset_database()
                        if success:
                            invalidate_cache()
                            for key in list(st.session_state.keys()):
                                del st.session_state[key]
                            st.success("Database reset complete!")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("Reset failed. Check logs for details.")
            with col2:
                if st.button("Cancel"):
                    st.session_state.show_reset_confirm = False
                    st.rerun()


def render_log_viewer():
    """Render the live logs viewer tab."""
    st.subheader("📜 Live Logs")
    st.caption(f"Showing last 50 lines from: {LOG_FILE}")
    
    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("🔄 Refresh Logs"):
            st.rerun()
    
    log_content = read_log_file(50)
    st.code(log_content, language="log")


def render_laws_tab():
    """Render the Sheriff's Code (Governance Rules) tab."""
    st.subheader("👮‍♂️ Sheriff's Code - Governance Rules")
    st.caption("These rules are automatically injected into all Devin prompts (Scope & Execute).")
    
    st.info(f"Rules file location: `{RULES_FILE}`")
    
    current_rules = load_governance_rules()
    
    edited_rules = st.text_area(
        "Edit Governance Rules",
        value=current_rules,
        height=400,
        help="These rules will be appended to every Devin prompt to enforce code standards."
    )
    
    col1, col2 = st.columns([1, 4])
    with col1:
        if st.button("💾 Save Rules", type="primary", use_container_width=True):
            if save_governance_rules(edited_rules):
                st.success("Rules saved successfully!")
                time.sleep(1)
                st.rerun()
            else:
                st.error("Failed to save rules. Check logs for details.")
    
    with col2:
        if st.button("🔄 Reset to Default", use_container_width=True):
            from devin_sheriff.devin_client import DEFAULT_RULES
            if save_governance_rules(DEFAULT_RULES):
                st.success("Rules reset to default!")
                time.sleep(1)
                st.rerun()
            else:
                st.error("Failed to reset rules.")
    
    st.markdown("---")
    st.markdown("### How It Works")
    st.markdown("""
    1. When you trigger **Scope** or **Execute**, these rules are appended to the system prompt.
    2. Devin will follow these rules when generating code.
    3. Use this to enforce team standards, security policies, or coding conventions.
    
    **Example Rules:**
    - "All API endpoints must have rate limiting"
    - "Database queries must use parameterized statements"
    - "No console.log statements in production code"
    """)


# --- WORKSPACE RENDERER ---
def render_issue_workspace(issue, repo, db):
    """Renders the detailed view and action buttons for a single issue."""
    
    c1, c2 = st.columns([2, 1])

    with c1:
        st.markdown(f"### Issue #{issue.number}: {issue.title}")
        with st.expander("📖 View Issue Description", expanded=True):
            st.markdown(issue.body if issue.body else "*No description provided.*")

        if issue.scope_json:
            # Check if scope_json contains an error (failed to parse Devin response)
            if "error" in issue.scope_json:
                st.error(f"**Scoping Failed:** {issue.scope_json.get('error', 'Unknown error')}")
                st.caption("Try re-scoping this issue or check the Live Logs for details.")
            else:
                st.success(f"**Plan Ready** (Confidence: {issue.confidence}%)")
            
            # FEATURE 2: Risk Level Badge (Suspect Profiling)
            files_to_change = issue.scope_json.get("files_to_change", [])
            risk_level, risk_color, risk_desc = analyze_risk_level(files_to_change)
            
            risk_emoji = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟢", "UNKNOWN": "⚪"}.get(risk_level, "⚪")
            st.markdown(f"**Risk Level:** {risk_emoji} :{risk_color}[{risk_level}]")
            st.caption(risk_desc)
            
            # Plan Editor Feature for SCOPED issues
            if issue.status == "SCOPED":
                render_plan_editor(issue, db)
                
                # FEATURE: The Tribunal (Plan Review)
                render_tribunal_section(issue, repo)
                
                # FEATURE 3: The Interrogation Room (Refine Plan)
                render_interrogation_room(issue, repo, db)
            else:
                with st.expander("📋 View Action Plan", expanded=True):
                    render_plan_display(issue.scope_json)

    with c2:
        st.markdown("### ⚡ Actions")
        
        status_color = {
            "NEW": "gray",
            "SCOPED": "orange",
            "EXECUTING": "blue",
            "PR_OPEN": "green",
            "DONE": "green"
        }.get(issue.status, "gray")
        
        st.caption(f"Current Status: **:{status_color}[{issue.status}]**")

        if issue.status == "PR_OPEN" and issue.pr_url:
            st.success(f"🚀 [View Pull Request]({issue.pr_url})")
            
            ci_badge = get_ci_badge(issue.ci_status or "unknown", issue.retry_count or 0)
            st.markdown(f"**CI Status:** {ci_badge}")
            
            if st.button("🔄 Check CI Status", key=f"check_ci_{issue.id}", use_container_width=True):
                with st.spinner("Checking CI status..."):
                    ci_result = check_and_update_ci_status(issue, repo, db)
                    if ci_result["status"] == "failing":
                        st.warning(f"CI is failing! {len(ci_result.get('failures', []))} check(s) failed.")
                        
                        if (issue.retry_count or 0) < 3:
                            st.markdown("**Auto-Healer Available**")
                            if st.button("🔧 Auto-Heal (Retry Fix)", key=f"auto_heal_{issue.id}", type="primary"):
                                heal_result = trigger_auto_heal(issue, repo, ci_result.get("failures", []), db)
                                if "error" not in heal_result:
                                    st.toast(f"🩹 Auto-Healing triggered for Issue #{issue.number}")
                                    
                                    st.info("Waiting 5 seconds before calling Devin (rate limiting)...")
                                    time.sleep(5)
                                    
                                    task_runner = st.session_state.task_runner
                                    plan_to_use = issue.scope_json or {}
                                    task_runner.run_execute(
                                        repo.url, issue.number, issue.title, plan_to_use,
                                        ci_failure_context=heal_result.get("failure_context")
                                    )
                                    st.info(f"Auto-Healer triggered (Retry {heal_result['retry_count']}/3)")
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.error(heal_result["error"])
                        else:
                            st.error("Max retries (3) reached. Manual intervention required.")
                    elif ci_result["status"] == "passing":
                        st.success("All CI checks passed!")
                    elif ci_result["status"] == "pending":
                        st.info("CI checks still running...")
                    st.rerun()
        
        st.markdown("---")

        # Check if async task is running
        task_runner = st.session_state.task_runner
        
        # Auto-check for task completion
        if task_runner.status == "completed":
            handle_task_completion(issue, task_runner, db)
            st.rerun()
        elif task_runner.status == "failed":
            st.error(f"Task failed: {task_runner.error}")
            task_runner.status = "idle"
        
        if task_runner.is_running():
            st.info("🔄 Task in progress...")
            progress = task_runner.get_progress()
            st.progress(progress / 100)
            
            # Auto-refresh every 2 seconds while task is running
            time.sleep(2)
            st.rerun()
        else:
            # BUTTON: SCOPE (Planning)
            if issue.status in ["NEW", "DONE"]:
                if st.button("🔍 Start Scoping", key=f"scope_{issue.id}", type="primary", use_container_width=True):
                    task_runner.run_scope(repo.url, issue.number, issue.title, issue.body, repo_id=repo.id)
                    st.info("Scoping started in background...")
                    time.sleep(1)
                    st.rerun()

            # BUTTON: EXECUTE (Coding)
            if issue.status == "SCOPED":
                plan_to_use = st.session_state.get('edited_plan') or issue.scope_json
                
                if st.button("🛠 Execute Fix", key=f"exec_{issue.id}", type="primary", use_container_width=True):
                    task_runner.run_execute(repo.url, issue.number, issue.title, plan_to_use)
                    st.info("Execution started in background...")
                    time.sleep(1)
                    st.rerun()
                
                if st.button("🔄 Re-Scope (Discard Plan)", key=f"rescope_{issue.id}", use_container_width=True):
                    issue.status = "NEW"
                    issue.scope_json = None
                    issue.confidence = 0
                    st.session_state.edited_plan = None
                    db.commit()
                    invalidate_cache()
                    st.rerun()

            # BUTTON: MARK AS CLOSED
            st.markdown("---")
            st.markdown("**Manual Controls**")
            
            if issue.status != "DONE":
                col_close1, col_close2 = st.columns(2)
                with col_close1:
                    if st.button("✅ Close Locally Only", key=f"close_local_{issue.id}", use_container_width=True):
                        result = close_issue_workflow(issue, repo, db, close_on_github=False)
                        st.success(f"Issue #{issue.number} marked as closed locally!")
                        invalidate_cache()
                        time.sleep(1)
                        st.rerun()
                
                with col_close2:
                    if st.button("✅ Close on GitHub", key=f"close_gh_{issue.id}", use_container_width=True):
                        st.session_state.show_close_confirm = issue.id
            
            if st.session_state.get('show_close_confirm') == issue.id:
                st.warning(f"Close issue #{issue.number} on GitHub?")
                st.caption("This will also close the issue remotely on GitHub.")
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Confirm Close on GitHub", key=f"confirm_close_{issue.id}", type="primary"):
                        result = close_issue_workflow(issue, repo, db, close_on_github=True)
                        st.session_state.show_close_confirm = None
                        
                        if result["success"]:
                            st.success(f"Issue #{issue.number} closed on GitHub!")
                            invalidate_cache()
                            time.sleep(1)
                            st.rerun()
                        else:
                            if result["error_type"] == "permission_denied":
                                st.error(result["error_message"])
                                st.info("💡 Tip: The issue was closed locally. To close on GitHub, generate a new token with 'repo' scope.")
                            else:
                                st.warning(f"Local status updated, but GitHub close failed: {result['error_message']}")
                            invalidate_cache()
                            time.sleep(2)
                            st.rerun()
                with col2:
                    if st.button("Cancel", key=f"cancel_close_{issue.id}"):
                        st.session_state.show_close_confirm = None
                        st.rerun()

            # BUTTON: RESET
            st.markdown("---")
            if st.button("🗑 Reset Issue State", key=f"reset_{issue.id}", use_container_width=True):
                issue.status = "NEW"
                issue.scope_json = None
                issue.confidence = None
                issue.pr_url = None
                st.session_state.edited_plan = None
                db.commit()
                invalidate_cache()
                st.rerun()


def render_plan_display(plan: dict):
    """Display the action plan in a readable format."""
    st.markdown("**Strategy:**")
    for step in plan.get("action_plan", []):
        st.markdown(f"- {step}")
    
    st.markdown("---")
    st.markdown("**Files Targeted:**")
    for f in plan.get("files_to_change", []):
        st.code(f, language="bash")


def render_plan_editor(issue, db):
    """Render the plan editor for cost & safety controls."""
    with st.expander("📋 View & Edit Action Plan", expanded=True):
        st.info("💡 **Cost Control:** Edit the plan below before executing to ensure Devin follows your preferred approach.")
        
        current_plan = st.session_state.get('edited_plan') or issue.scope_json
        
        plan_json_str = json.dumps(current_plan, indent=2)
        
        edited_json = st.text_area(
            "Edit Plan JSON:",
            value=plan_json_str,
            height=300,
            key=f"plan_editor_{issue.id}"
        )
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 Save Edited Plan", key=f"save_plan_{issue.id}"):
                try:
                    parsed_plan = json.loads(edited_json)
                    st.session_state.edited_plan = parsed_plan
                    st.success("Plan saved! Click 'Execute Fix' to use this plan.")
                except json.JSONDecodeError as e:
                    st.error(f"Invalid JSON: {e}")
        
        with col2:
            if st.button("↩️ Reset to Original", key=f"reset_plan_{issue.id}"):
                st.session_state.edited_plan = None
                st.rerun()
        
        if st.session_state.get('edited_plan'):
            st.warning("Using edited plan (not saved to database)")


def render_interrogation_room(issue, repo, db):
    """
    Feature 3: The Interrogation Room - Refine plans with natural language feedback.
    Allows users to provide instructions to improve the generated plan.
    """
    with st.expander("👮 Interrogation Room (Refine Plan)", expanded=False):
        st.info("💡 **Refine the plan:** Provide instructions to improve Devin's approach. "
                "Example: 'Don't touch main.py, create a helper file instead.'")
        
        refinement_notes = st.text_area(
            "Your refinement instructions:",
            placeholder="e.g., 'Focus only on the API layer, don't modify the database models.'",
            height=100,
            key=f"interrogate_{issue.id}"
        )
        
        if st.button("🔄 Re-Scope with Feedback", key=f"refine_{issue.id}", type="primary", use_container_width=True):
            if not refinement_notes.strip():
                st.warning("Please provide refinement instructions first.")
            else:
                with st.spinner("👮 Interrogating Devin with your feedback... (1-3 minutes)"):
                    try:
                        cfg = load_config()
                        client = DevinClient(cfg)
                        
                        new_plan = client.start_rescope_session(
                            repo.url,
                            issue.number,
                            issue.title,
                            issue.body or "",
                            issue.scope_json,
                            refinement_notes
                        )
                        
                        issue.scope_json = new_plan
                        issue.confidence = new_plan.get("confidence", 0)
                        db.commit()
                        invalidate_cache()
                        
                        st.success("Plan refined successfully!")
                        if new_plan.get("refinement_applied"):
                            st.info(f"📝 {new_plan['refinement_applied']}")
                        time.sleep(1)
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f"Re-scope failed: {str(e)}")
                        logger.error(f"Interrogation failed for issue #{issue.number}: {e}")


def handle_task_completion(issue, task_runner, db):
    """Handle completion of async scope/execute tasks."""
    if task_runner.result:
        if 'pr_url' in task_runner.result:
            issue.status = "PR_OPEN"
            issue.pr_url = task_runner.result.get("pr_url")
            logger.info(f"PR created for issue #{issue.number}: {issue.pr_url}")
            st.toast("🚀 PR Created Successfully!", icon="🔥")
        else:
            issue.scope_json = task_runner.result
            issue.confidence = task_runner.result.get("confidence", 0)
            issue.status = "SCOPED"
            logger.info(f"Scoping completed for issue #{issue.number}")
            st.toast("✅ Scoping Complete!", icon="🎉")
        
        db.commit()
        invalidate_cache()
    
    task_runner.status = "idle"
    task_runner.result = None


def close_issue_workflow(issue, repo, db, close_on_github: bool = False) -> Dict[str, Any]:
    """
    Close an issue locally and optionally on GitHub.
    Returns dict with 'success', 'local_closed', 'github_closed', 'error_message'.
    """
    issue.status = "DONE"
    issue.state = "closed"
    db.commit()
    logger.info(f"Issue #{issue.number} marked as closed locally")
    
    result = {
        "success": True,
        "local_closed": True,
        "github_closed": False,
        "error_message": None,
        "error_type": None
    }
    
    if close_on_github:
        try:
            cfg = load_config()
            gh = GitHubClient(cfg)
            
            match = re.search(r"github\.com/([^/]+)/([^/]+)", repo.url)
            if match:
                owner, repo_name = match.groups()
                repo_name = repo_name.replace(".git", "")
                gh_result = gh.close_issue(owner, repo_name, issue.number)
                
                if gh_result["success"]:
                    logger.info(f"Issue #{issue.number} closed on GitHub")
                    result["github_closed"] = True
                else:
                    result["success"] = False
                    result["error_message"] = gh_result["message"]
                    result["error_type"] = gh_result["error_type"]
        except Exception as e:
            logger.error(f"Failed to close issue on GitHub: {e}")
            result["success"] = False
            result["error_message"] = str(e)
            result["error_type"] = "unknown"
    
    return result


# --- LEGACY ACTION HANDLERS (kept for compatibility) ---
def run_scope_action(repo, issue, db):
    """Handles calling Devin to SCOPE an issue."""
    try:
        with st.spinner("🤖 Devin is analyzing the codebase... (30-60s)"):
            cfg = load_config()
            client = DevinClient(cfg)
            
            plan = client.start_scope_session(
                repo.url,
                issue.number,
                issue.title,
                issue.body
            )
            
            issue.scope_json = plan
            issue.confidence = plan.get("confidence", 0)
            issue.status = "SCOPED"
            db.commit()
            invalidate_cache()
        
        st.toast("✅ Scoping Complete!", icon="🎉")
        time.sleep(1)
        st.rerun()

    except Exception as e:
        st.error(f"Devin API Error: {str(e)}")


def run_execute_action(repo, issue, db):
    """Handles calling Devin to EXECUTE a fix."""
    try:
        plan_to_use = st.session_state.get('edited_plan') or issue.scope_json
        
        with st.spinner("👨‍💻 Devin is coding, testing, and pushing... (2-5 mins)"):
            cfg = load_config()
            client = DevinClient(cfg)
            
            result = client.start_execute_session(
                repo.url,
                issue.number,
                issue.title,
                plan_to_use
            )
            
            issue.status = "PR_OPEN"
            issue.pr_url = result.get("pr_url")
            st.session_state.edited_plan = None
            db.commit()
            invalidate_cache()
        
        st.toast("🚀 PR Created Successfully!", icon="🔥")
        time.sleep(1)
        st.rerun()

    except Exception as e:
        st.error(f"Execution Failed: {str(e)}")


def render_wanted_tab(selected_repo):
    """Render the Wanted List (Tech Debt Scanner) tab."""
    st.subheader("📜 The Wanted List - Tech Debt Scanner")
    st.caption("Scan your local repository for TODO, FIXME, and other tech debt markers.")
    
    st.markdown("---")
    st.markdown("**Enter Local Repository Path**")
    st.caption("This should be the path to a local clone of your repository.")
    
    default_path = f"/home/ubuntu/repos/{selected_repo.name}"
    repo_path = st.text_input("Local Repo Path", value=default_path, key="wanted_repo_path")
    
    col1, col2 = st.columns([1, 4])
    with col1:
        scan_button = st.button("🔍 Scan for TODOs", type="primary", use_container_width=True)
    
    if scan_button:
        with st.spinner("Scanning repository for tech debt..."):
            todos = scan_for_todos(repo_path)
            st.session_state.wanted_todos = todos
            st.session_state.wanted_scanned_path = repo_path
    
    if 'wanted_todos' in st.session_state and st.session_state.wanted_todos:
        todos = st.session_state.wanted_todos
        
        st.success(f"Found {len(todos)} tech debt items!")
        
        tag_counts = {}
        for t in todos:
            tag_counts[t['tag']] = tag_counts.get(t['tag'], 0) + 1
        
        cols = st.columns(len(tag_counts))
        for i, (tag, count) in enumerate(tag_counts.items()):
            cols[i].metric(tag, count)
        
        st.markdown("---")
        
        for idx, todo in enumerate(todos[:50]):
            with st.container():
                col1, col2, col3 = st.columns([3, 1, 1])
                
                with col1:
                    tag_color = {"TODO": "blue", "FIXME": "red", "XXX": "orange", "HACK": "orange", "BUG": "red"}.get(todo['tag'], "gray")
                    st.markdown(f"**:{tag_color}[{todo['tag']}]** `{todo['file']}:{todo['line']}`")
                    st.caption(todo['comment'])
                
                with col2:
                    st.caption(f"Line {todo['line']}")
                
                with col3:
                    if st.button("⭐ Create Issue", key=f"create_issue_{idx}", use_container_width=True):
                        title = f"Refactor: {todo['comment'][:50]}..."
                        body = (
                            f"**Tech Debt Found by Sheriff**\n\n"
                            f"**Type:** {todo['tag']}\n"
                            f"**File:** `{todo['file']}`\n"
                            f"**Line:** {todo['line']}\n\n"
                            f"**Comment:**\n```\n{todo['comment']}\n```\n\n"
                            f"---\n*Created by Devin Sheriff's Wanted List scanner*"
                        )
                        
                        try:
                            cfg = load_config()
                            gh = GitHubClient(cfg)
                            result = gh.create_issue(selected_repo.owner, selected_repo.name, title, body)
                            
                            if result["success"]:
                                st.toast(f"Issue #{result['issue_number']} created!", icon="⭐")
                                sync_repo_issues(selected_repo.url)
                                invalidate_cache()
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.error(result["error"])
                        except Exception as e:
                            st.error(f"Failed to create issue: {e}")
                
                st.markdown("---")
        
        if len(todos) > 50:
            st.info(f"Showing first 50 of {len(todos)} items. Clean up some tech debt!")
    
    elif 'wanted_todos' in st.session_state and not st.session_state.wanted_todos:
        st.success("No tech debt found! Your codebase is clean.")


def render_tribunal_section(issue, repo):
    """Render The Tribunal (Plan Review) section."""
    st.markdown("---")
    st.markdown("### ⚖️ The Tribunal - Plan Review")
    st.caption("Get an AI-powered grade on your plan before execution.")
    
    if 'tribunal_result' not in st.session_state:
        st.session_state.tribunal_result = None
    
    col1, col2 = st.columns([1, 3])
    
    with col1:
        if st.button("⚖️ Convene Tribunal", type="secondary", use_container_width=True):
            with st.spinner("The Tribunal is reviewing the plan..."):
                try:
                    cfg = load_config()
                    client = DevinClient(cfg)
                    result = client.start_tribunal_session(issue.scope_json)
                    st.session_state.tribunal_result = result
                except Exception as e:
                    st.error(f"Tribunal failed: {e}")
    
    if st.session_state.tribunal_result:
        result = st.session_state.tribunal_result
        
        if "error" in result and result.get("grade") == "?":
            st.error(f"Tribunal error: {result.get('error')}")
        else:
            grade = result.get("grade", "?")
            grade_color = get_tribunal_grade_color(grade)
            
            st.markdown(f"## Grade: :{grade_color}[{grade}]")
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Safety", f"{result.get('safety_score', '?')}/10")
            col2.metric("Efficiency", f"{result.get('efficiency_score', '?')}/10")
            col3.metric("Completeness", f"{result.get('completeness_score', '?')}/10")
            
            if result.get("critique"):
                st.markdown("**Critique:**")
                st.info(result["critique"])
            
            if result.get("recommendations"):
                st.markdown("**Recommendations:**")
                for rec in result["recommendations"]:
                    st.markdown(f"- {rec}")
            
            if grade in ["D", "F"]:
                st.warning("⚠️ **Tribunal advises against execution.** Consider refining the plan first.")
            
            if st.button("🔄 Clear Tribunal Result"):
                st.session_state.tribunal_result = None
                st.rerun()


if __name__ == "__main__":
    main()
