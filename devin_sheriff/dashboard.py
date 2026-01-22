import sys
import os
from pathlib import Path
import time

# --- FIX IMPORT PATHS ---
current_dir = Path(__file__).parent
root_dir = current_dir.parent
sys.path.append(str(root_dir))
# ------------------------

import streamlit as st
import pandas as pd
from devin_sheriff.models import SessionLocal, Repo, Issue
from devin_sheriff.devin_client import DevinClient
from devin_sheriff.config import load_config
from devin_sheriff.sync import sync_repo_issues

# Page Config
st.set_page_config(page_title="Devin Sheriff", page_icon="🤠", layout="wide")
st.title("🤠 Devin Sheriff (Local)")

def get_db():
    return SessionLocal()

# Sidebar: Repos
st.sidebar.header("Repositories")
db = get_db()
repos = db.query(Repo).all()

if not repos:
    st.sidebar.warning("No repos connected.")
    st.sidebar.info("Run `python main.py connect <url>`")
else:
    repo_names = [r.name for r in repos]
    selected_repo_name = st.sidebar.selectbox("Select Repo", repo_names)
    selected_repo = next(r for r in repos if r.name == selected_repo_name)

    # --- FIXED SYNC BUTTON ---
    if st.sidebar.button("🔄 Sync Issues from GitHub"):
        # Correct way to show spinner in sidebar
        with st.sidebar:
            with st.spinner("Syncing..."):
                msg = sync_repo_issues(selected_repo.url)
        st.sidebar.success(msg)
        time.sleep(1)
        st.rerun()
    # -----------------------

    st.header(f"Issues: {selected_repo.owner}/{selected_repo.name}")

    # Fetch ONLY OPEN Issues for this Repo
    issues = db.query(Issue).filter(
        Issue.repo_id == selected_repo.id, 
        Issue.state == "open"
    ).all()
    
    db.close()

    if not issues:
        st.info("No open issues found.")
    else:
        # Table View
        data = []
        for i in issues:
            data.append({
                "ID": i.id,
                "Number": f"#{i.number}",
                "Title": i.title,
                "Status": i.status,
                "Confidence": f"{i.confidence}%" if i.confidence else "-",
            })
        
        st.dataframe(pd.DataFrame(data), hide_index=True, use_container_width=True)

        # Detail & Actions
        st.divider()
        st.subheader("Issue Actions")
        
        # Dropdown: Ensure unique list
        issue_options = sorted([f"#{i.number}: {i.title}" for i in issues], key=lambda x: int(x.split(":")[0].replace("#", "")))
        issue_selection = st.selectbox("Select Issue", issue_options)
        
        if issue_selection:
            num = int(issue_selection.split(":")[0].replace("#", ""))
            
            # Re-fetch specific issue
            db = get_db()
            selected_issue = db.query(Issue).filter(Issue.repo_id == selected_repo.id, Issue.number == num).first()

            # SHOW PR LINK IF OPEN
            if selected_issue.status == "PR_OPEN" and selected_issue.pr_url:
                st.success(f"🚀 **Fix Deployed!** Pull Request: [{selected_issue.pr_url}]({selected_issue.pr_url})")

            col1, col2 = st.columns([2, 1])
            
            with col1:
                st.markdown(f"### {selected_issue.title}")
                st.info(selected_issue.body)

                # --- SHOW PLAN IF EXISTS ---
                if selected_issue.scope_json:
                    st.divider()
                    st.success(f"✅ Scoped (Confidence: {selected_issue.confidence}%)")
                    
                    scope = selected_issue.scope_json
                    st.markdown("#### 📋 Action Plan")
                    for step in scope.get("action_plan", []):
                        st.markdown(f"- {step}")
                    
                    st.markdown("#### 📂 Files to Change")
                    st.code("\n".join(scope.get("files_to_change", [])), language="text")

            with col2:
                st.write(f"**Status:** {selected_issue.status}")
                
                # RESET BUTTON
                if st.button("🔄 Reset Issue", use_container_width=True):
                    selected_issue.status = "NEW"
                    selected_issue.scope_json = None
                    selected_issue.confidence = None
                    selected_issue.pr_url = None
                    db.commit()
                    st.rerun()

                # SCOPE BUTTON
                if st.button("🔍 Start Scope (Plan)", type="primary", use_container_width=True, disabled=selected_issue.status in ["SCOPED", "EXECUTING", "PR_OPEN"]):
                    with st.spinner("Contacting Devin API... (This may take 30-60 seconds)"):
                        try:
                            cfg = load_config()
                            client = DevinClient(cfg)
                            
                            plan = client.start_scope_session(
                                selected_repo.url,
                                selected_issue.number, 
                                selected_issue.title, 
                                selected_issue.body
                            )
                            
                            selected_issue.scope_json = plan
                            selected_issue.confidence = plan.get("confidence", 0)
                            selected_issue.status = "SCOPED"
                            db.commit()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Devin API Error: {e}")

                # EXECUTE BUTTON
                if st.button("🛠 Execute Fix", type="secondary", use_container_width=True, disabled=selected_issue.status != "SCOPED"):
                    with st.spinner("Devin is writing code... (This may take 2-5 minutes)"):
                        try:
                            cfg = load_config()
                            client = DevinClient(cfg)
                            
                            result = client.start_execute_session(
                                selected_repo.url,
                                selected_issue.number, 
                                selected_issue.title, 
                                selected_issue.scope_json
                            )
                            
                            selected_issue.status = "PR_OPEN"
                            selected_issue.pr_url = result.get("pr_url")
                            db.commit()
                            st.rerun()
                            
                        except Exception as e:
                            st.error(f"Execution failed: {e}")
            
            db.close()