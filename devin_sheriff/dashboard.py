import sys
import os
from pathlib import Path

# --- FIX IMPORT PATHS ---
# Add the project root to Python's search path so we can import our modules
# regardless of how Streamlit is started.
current_dir = Path(__file__).parent
root_dir = current_dir.parent
sys.path.append(str(root_dir))
# ------------------------

import streamlit as st
import pandas as pd
from devin_sheriff.models import SessionLocal, Repo, Issue, DevinSession

# Page Config
st.set_page_config(page_title="Devin Sheriff", page_icon="🤠", layout="wide")

st.title("🤠 Devin Sheriff (Local)")

# Database Helper
def get_db():
    return SessionLocal()

# Sidebar: Repos
st.sidebar.header("Repositories")
db = get_db()
repos = db.query(Repo).all()
db.close()

if not repos:
    st.sidebar.warning("No repos connected yet.")
    st.sidebar.info("Run `python main.py connect <url>` in terminal.")
else:
    # Sidebar Selection
    repo_names = [r.name for r in repos]
    selected_repo_name = st.sidebar.selectbox("Select Repo", repo_names)
    
    # Get ID of selected repo
    selected_repo = next(r for r in repos if r.name == selected_repo_name)

    # Main Content
    st.header(f"Issues: {selected_repo.owner}/{selected_repo.name}")

    # Fetch Issues for this Repo
    db = get_db()
    issues = db.query(Issue).filter(Issue.repo_id == selected_repo.id).all()
    db.close()

    if not issues:
        st.info("No open issues found.")
    else:
        # Convert to DataFrame for a clean table
        data = []
        for i in issues:
            data.append({
                "ID": i.id,
                "Number": f"#{i.number}",
                "Title": i.title,
                "Status": i.status,
                "Confidence": f"{i.confidence}%" if i.confidence else "-",
                "State": i.state
            })
        
        df = pd.DataFrame(data)
        
        # Display Interactive Table
        st.dataframe(
            df, 
            column_config={
                "ID": None, # Hide ID column
                "Number": st.column_config.TextColumn("Issue #", width="small"),
                "Title": st.column_config.TextColumn("Title", width="large"),
                "Status": st.column_config.SelectboxColumn(
                    "Sheriff Status",
                    options=["NEW", "SCOPING", "SCOPED", "EXECUTING", "DONE"],
                    width="medium"
                ),
            },
            hide_index=True,
            use_container_width=True
        )

        # Issue Detail View
        st.divider()
        st.subheader("Issue Actions")
        
        issue_selection = st.selectbox(
            "Select Issue to Manage", 
            [f"#{i.number}: {i.title}" for i in issues]
        )
        
        if issue_selection:
            # Parse the selected string to get the number
            num = int(issue_selection.split(":")[0].replace("#", ""))
            selected_issue = next(i for i in issues if i.number == num)

            col1, col2 = st.columns([2, 1])
            
            with col1:
                st.markdown(f"**{selected_issue.title}**")
                st.text_area("Description", selected_issue.body, height=200, disabled=True)
            
            with col2:
                st.write(f"**Current Status:** {selected_issue.status}")
                
                # Placeholder Buttons (Logic comes in Phase 4/5)
                if st.button("🔍 Start Scope (Plan)", type="primary", use_container_width=True):
                    st.toast("Scoping logic coming in Phase 4!")
                
                if st.button("🛠 Execute Fix", type="secondary", use_container_width=True):
                    st.toast("Execution logic coming in Phase 5!")