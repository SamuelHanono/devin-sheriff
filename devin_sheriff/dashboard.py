import sys
import os
import re
import json
import threading
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
import pandas as pd
import streamlit as st

# --- FIX IMPORT PATHS ---
current_dir = Path(__file__).parent
root_dir = current_dir.parent
sys.path.append(str(root_dir))
# ------------------------

from devin_sheriff.models import SessionLocal, Repo, Issue, reset_database, get_db_path, init_db
from devin_sheriff.devin_client import DevinClient
from devin_sheriff.config import load_config, CONFIG_DIR
from devin_sheriff.sync import sync_repo_issues, sync_pr_statuses
from devin_sheriff.github_client import GitHubClient

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
    
    def run_scope(self, repo_url: str, issue_number: int, title: str, body: str):
        """Run scoping in background."""
        self.status = "running"
        self.progress = 0
        self.error = None
        self.task_type = "scope"
        
        def task():
            try:
                self.progress = 10
                cfg = load_config()
                client = DevinClient(cfg)
                
                self.progress = 30
                plan = client.start_scope_session(repo_url, issue_number, title, body)
                
                self.progress = 100
                self.result = plan
                self.status = "completed"
            except Exception as e:
                self.error = str(e)
                self.status = "failed"
        
        self.thread = threading.Thread(target=task, daemon=True)
        self.thread.start()
    
    def run_execute(self, repo_url: str, issue_number: int, title: str, plan_json: dict):
        """Run execution in background."""
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
                result = client.start_execute_session(repo_url, issue_number, title, plan_json)
                
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

# --- MAIN DASHBOARD LOGIC ---
def main():
    st.title("🤠 Devin Sheriff v2.0")

    # 1. SIDEBAR: REPOSITORY SELECTION
    st.sidebar.header("📂 Repository")
    repos = get_cached_repos()
    
    if not repos:
        st.sidebar.warning("No repositories connected.")
        st.sidebar.info("Run `python main.py connect <url>` in your terminal.")
        render_danger_zone()
        return

    repo_names = [r.name for r in repos]
    selected_repo_name = st.sidebar.selectbox("Select Repository", repo_names)
    selected_repo = next(r for r in repos if r.name == selected_repo_name)

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

    # Render Danger Zone in sidebar
    render_danger_zone()
    
    # 3. MAIN CONTENT AREA WITH TABS
    tab_main, tab_logs = st.tabs(["🛠 Issue Management", "📜 Live Logs"])
    
    with tab_main:
        render_main_dashboard(selected_repo, filter_status)
    
    with tab_logs:
        render_log_viewer()


def render_main_dashboard(selected_repo, filter_status):
    """Render the main issue management dashboard."""
    db = get_db()
    
    try:
        all_issues = db.query(Issue).filter(
            Issue.repo_id == selected_repo.id,
            Issue.state == "open"
        ).all()
        
        count_new = len([i for i in all_issues if i.status == "NEW"])
        count_scoped = len([i for i in all_issues if i.status == "SCOPED"])
        count_pr = len([i for i in all_issues if i.status == "PR_OPEN"])

        m1, m2, m3 = st.columns(3)
        m1.metric("New Issues", count_new)
        m2.metric("Scoped & Planned", count_scoped)
        m3.metric("PRs Open", count_pr)

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
            return

        st.subheader(f"🛠 Managing Issues ({len(display_issues)})")
        
        issue_options = {f"#{i.number}: {i.title} [{i.status}]": i.number for i in display_issues}
        selected_label = st.selectbox("Select an Issue to work on:", options=list(issue_options.keys()))
        
        if selected_label:
            selected_number = issue_options[selected_label]
            current_issue = db.query(Issue).filter(
                Issue.repo_id == selected_repo.id, 
                Issue.number == selected_number
            ).first()

            render_issue_workspace(current_issue, selected_repo, db)
    finally:
        db.close()


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

# --- WORKSPACE RENDERER ---
def render_issue_workspace(issue, repo, db):
    """Renders the detailed view and action buttons for a single issue."""
    
    c1, c2 = st.columns([2, 1])

    with c1:
        st.markdown(f"### {issue.title}")
        with st.expander("📖 View Issue Description", expanded=True):
            st.markdown(issue.body if issue.body else "*No description provided.*")

        if issue.scope_json:
            st.success(f"**Plan Ready** (Confidence: {issue.confidence}%)")
            
            # Plan Editor Feature for SCOPED issues
            if issue.status == "SCOPED":
                render_plan_editor(issue, db)
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
        
        st.markdown("---")

        # Check if async task is running
        task_runner = st.session_state.task_runner
        
        if task_runner.is_running():
            st.info("🔄 Task in progress...")
            progress = task_runner.get_progress()
            st.progress(progress / 100)
            
            if st.button("Check Status"):
                if task_runner.status == "completed":
                    handle_task_completion(issue, task_runner, db)
                elif task_runner.status == "failed":
                    st.error(f"Task failed: {task_runner.error}")
                    task_runner.status = "idle"
                st.rerun()
        else:
            # BUTTON: SCOPE (Planning)
            if issue.status in ["NEW", "DONE"]:
                if st.button("🔍 Start Scoping", key=f"scope_{issue.id}", type="primary", use_container_width=True):
                    task_runner.run_scope(repo.url, issue.number, issue.title, issue.body)
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
                if st.button("✅ Mark as Closed", key=f"close_{issue.id}", use_container_width=True):
                    st.session_state.show_close_confirm = issue.id
            
            if st.session_state.get('show_close_confirm') == issue.id:
                st.warning("Close this issue?")
                
                close_on_github = st.checkbox("Also close on GitHub", value=True, key=f"gh_close_{issue.id}")
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Confirm Close", key=f"confirm_close_{issue.id}"):
                        success = close_issue_workflow(issue, repo, db, close_on_github)
                        st.session_state.show_close_confirm = None
                        if success:
                            st.success("Issue closed!")
                            invalidate_cache()
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.warning("Local status updated, but failed to close on GitHub")
                            invalidate_cache()
                            time.sleep(1)
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


def close_issue_workflow(issue, repo, db, close_on_github: bool = False) -> bool:
    """Close an issue locally and optionally on GitHub."""
    issue.status = "DONE"
    issue.state = "closed"
    db.commit()
    logger.info(f"Issue #{issue.number} marked as closed locally")
    
    if close_on_github:
        try:
            cfg = load_config()
            gh = GitHubClient(cfg)
            
            match = re.search(r"github\.com/([^/]+)/([^/]+)", repo.url)
            if match:
                owner, repo_name = match.groups()
                repo_name = repo_name.replace(".git", "")
                success = gh.close_issue(owner, repo_name, issue.number)
                if success:
                    logger.info(f"Issue #{issue.number} closed on GitHub")
                return success
        except Exception as e:
            logger.error(f"Failed to close issue on GitHub: {e}")
            return False
    
    return True


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


if __name__ == "__main__":
    main()
