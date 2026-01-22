import sys
import os
from pathlib import Path
import time
import pandas as pd
import streamlit as st

# --- FIX IMPORT PATHS ---
current_dir = Path(__file__).parent
root_dir = current_dir.parent
sys.path.append(str(root_dir))
# ------------------------

from devin_sheriff.models import SessionLocal, Repo, Issue
from devin_sheriff.devin_client import DevinClient
from devin_sheriff.config import load_config
from devin_sheriff.sync import sync_repo_issues

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title="Devin Sheriff",
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
</style>
""", unsafe_allow_html=True)

# --- DATABASE HELPER ---
def get_db():
    return SessionLocal()

# --- MAIN DASHBOARD LOGIC ---
def main():
    st.title("🤠 Devin Sheriff (Local)")

    # 1. SIDEBAR: REPOSITORY SELECTION
    st.sidebar.header("📂 Repository")
    db = get_db()
    repos = db.query(Repo).all()
    
    if not repos:
        st.sidebar.warning("No repositories connected.")
        st.sidebar.info("Run `python main.py connect <url>` in your terminal.")
        db.close()
        return

    repo_names = [r.name for r in repos]
    selected_repo_name = st.sidebar.selectbox("Select Repository", repo_names)
    selected_repo = next(r for r in repos if r.name == selected_repo_name)

    # 2. SIDEBAR: SYNC & FILTERS
    col_sync, col_status = st.sidebar.columns([1, 2])
    
    # Sync Button
    if st.sidebar.button("🔄 Sync with GitHub", use_container_width=True):
        with st.sidebar:
            with st.spinner(f"Syncing {selected_repo.name}..."):
                try:
                    msg = sync_repo_issues(selected_repo.url)
                    st.success(msg)
                    time.sleep(1.5)
                    st.rerun()
                except Exception as e:
                    st.error(f"Sync failed: {e}")

    # View Filter
    filter_status = st.sidebar.radio(
        "Filter View",
        ["All Open Issues", "New / Untouched", "Scoped (Ready to Fix)", "PR Open"],
        index=0
    )

    st.sidebar.divider()
    
    # 3. METRICS OVERVIEW
    # Fetch issues based on repo
    all_issues = db.query(Issue).filter(
        Issue.repo_id == selected_repo.id,
        Issue.state == "open"
    ).all()
    
    # Calculate counts
    count_new = len([i for i in all_issues if i.status == "NEW"])
    count_scoped = len([i for i in all_issues if i.status == "SCOPED"])
    count_pr = len([i for i in all_issues if i.status == "PR_OPEN"])

    # Display Metrics Row
    m1, m2, m3 = st.columns(3)
    m1.metric("New Issues", count_new)
    m2.metric("Scoped & Planned", count_scoped)
    m3.metric("PRs Open", count_pr)

    st.divider()

    # 4. FILTERING LOGIC
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
        db.close()
        return

    # 5. ISSUE SELECTION & ACTION AREA
    st.subheader(f"🛠 Managing Issues ({len(display_issues)})")
    
    # Create a nice label for the dropdown
    issue_options = {f"#{i.number}: {i.title} [{i.status}]": i.number for i in display_issues}
    selected_label = st.selectbox("Select an Issue to work on:", options=list(issue_options.keys()))
    
    if selected_label:
        selected_number = issue_options[selected_label]
        # Re-fetch fresh object to ensure we have latest DB state
        current_issue = db.query(Issue).filter(
            Issue.repo_id == selected_repo.id, 
            Issue.number == selected_number
        ).first()

        render_issue_workspace(current_issue, selected_repo, db)

    db.close()

# --- WORKSPACE RENDERER ---
def render_issue_workspace(issue, repo, db):
    """Renders the detailed view and action buttons for a single issue."""
    
    # Layout: 2 Columns (Details | Actions)
    c1, c2 = st.columns([2, 1])

    with c1:
        st.markdown(f"### {issue.title}")
        with st.expander("📖 View Issue Description", expanded=True):
            st.markdown(issue.body if issue.body else "*No description provided.*")

        # Display AI Plan if it exists
        if issue.scope_json:
            st.success(f"**Plan Ready** (Confidence: {issue.confidence}%)")
            
            with st.expander("📋 View Action Plan", expanded=True):
                plan = issue.scope_json
                st.markdown("**Strategy:**")
                for step in plan.get("action_plan", []):
                    st.markdown(f"- {step}")
                
                st.markdown("---")
                st.markdown("**Files Targeted:**")
                for f in plan.get("files_to_change", []):
                    st.code(f, language="bash")

    with c2:
        st.markdown("### ⚡ Actions")
        
        # Status Badge
        status_color = {
            "NEW": "gray",
            "SCOPED": "orange",
            "EXECUTING": "blue",
            "PR_OPEN": "green",
            "DONE": "green"
        }.get(issue.status, "gray")
        
        st.caption(f"Current Status: **:{status_color}[{issue.status}]**")

        # PR Link
        if issue.status == "PR_OPEN" and issue.pr_url:
            st.success(f"🚀 [View Pull Request]({issue.pr_url})")
        
        st.markdown("---")

        # BUTTON: SCOPE (Planning)
        if issue.status in ["NEW", "DONE"]: # Allow re-scoping if done/new
            if st.button("🔍 Start Scoping", key=f"scope_{issue.id}", type="primary", use_container_width=True):
                run_scope_action(repo, issue, db)

        # BUTTON: EXECUTE (Coding)
        if issue.status == "SCOPED":
            if st.button("🛠 Execute Fix", key=f"exec_{issue.id}", type="primary", use_container_width=True):
                run_execute_action(repo, issue, db)
            
            if st.button("🔄 Re-Scope (Discard Plan)", key=f"rescope_{issue.id}", use_container_width=True):
                issue.status = "NEW"
                issue.scope_json = None
                issue.confidence = 0
                db.commit()
                st.rerun()

        # BUTTON: RESET
        st.markdown("---")
        if st.button("🗑 Reset Issue State", key=f"reset_{issue.id}", use_container_width=True):
            issue.status = "NEW"
            issue.scope_json = None
            issue.confidence = None
            issue.pr_url = None
            db.commit()
            st.rerun()

# --- ACTION HANDLERS ---
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
            
            # Update DB
            issue.scope_json = plan
            issue.confidence = plan.get("confidence", 0)
            issue.status = "SCOPED"
            db.commit()
        
        st.toast("✅ Scoping Complete!", icon="🎉")
        time.sleep(1)
        st.rerun()

    except Exception as e:
        st.error(f"Devin API Error: {str(e)}")

def run_execute_action(repo, issue, db):
    """Handles calling Devin to EXECUTE a fix."""
    try:
        with st.spinner("👨‍💻 Devin is coding, testing, and pushing... (2-5 mins)"):
            cfg = load_config()
            client = DevinClient(cfg)
            
            result = client.start_execute_session(
                repo.url,
                issue.number,
                issue.title,
                issue.scope_json
            )
            
            # Update DB
            issue.status = "PR_OPEN"
            issue.pr_url = result.get("pr_url")
            db.commit()
        
        st.toast("🚀 PR Created Successfully!", icon="🔥")
        time.sleep(1)
        st.rerun()

    except Exception as e:
        st.error(f"Execution Failed: {str(e)}")

if __name__ == "__main__":
    main()