# Devin Sheriff

A local dashboard tool that connects GitHub repositories to Devin AI for automated issue scoping and execution.

## Overview

Devin Sheriff provides a two-stage workflow for handling GitHub issues:

1. **Scoping** - Devin AI analyzes an issue and generates a structured action plan with confidence scores, files to change, and implementation steps.

2. **Execution** - Devin AI implements the approved plan, writes code, runs tests, and opens a Pull Request.

All data is stored locally on your machine. Your API keys never leave your system except when communicating with GitHub and Devin APIs.

## Installation

```bash
# Clone the repository
git clone https://github.com/SamuelHanono/devin-sheriff.git
cd devin-sheriff

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Setup

Run the interactive setup to configure your API keys:

```bash
python main.py setup
```

**Expected output:**
```
╭─────────────────────────────╮
│ Devin Sheriff (Local) Setup │
╰─────────────────────────────╯
Enter GitHub PAT (Personal Access Token): *****************
Enter Devin API Key: ***********************
✓ Configuration saved to ~/.devin-sheriff/config.json

Verifying GitHub connection...
✓ GitHub Connected as: YourUsername

Verifying Devin connection...
✓ Devin API Key Stored
```

## Usage

### Connect a Repository

```bash
python main.py connect https://github.com/owner/repo-name
```

**Expected output:**
```
Connecting to owner/repo-name...
✓ Repo 'repo-name' added to database.
Fetching open issues...
✓ Synced 12 open issues (12 new).
```

### Launch the Dashboard

```bash
streamlit run devin_sheriff/dashboard.py
```

The dashboard opens at `http://localhost:8501` and displays:

- **Sidebar**: Repository selector, sync controls, settings, and danger zone
- **Main View**: Issue list with status filters
- **Issue Workspace**: Detailed view with Scope and Execute buttons

### Workflow

1. Select an issue from the dropdown
2. Click **Start Scoping** to generate an action plan (30-60 seconds)
3. Review and optionally edit the plan in the Plan Editor
4. Click **Execute Fix** to implement the solution (2-10 minutes)
5. A Pull Request link appears when complete

## Issue Statuses

| Status | Description |
|--------|-------------|
| NEW | Issue fetched but not analyzed |
| SCOPED | Action plan generated |
| PR_OPEN | Pull Request created |
| DONE | Issue closed |

## Local Storage

All data is stored in `~/.devin-sheriff/`:

| File | Purpose |
|------|---------|
| `config.json` | API keys (GitHub PAT, Devin API Key) |
| `sheriff.db` | SQLite database (repos, issues, sessions) |
| `sheriff.log` | Application logs |

## Troubleshooting

### Reset the Database

If issues appear out of sync or you want a fresh start:

**Option 1: Dashboard**
1. Open the sidebar
2. Expand "Danger Zone"
3. Click "Delete All Data & Reset"
4. Confirm the action

**Option 2: Command Line**
```bash
rm ~/.devin-sheriff/sheriff.db
```

Then restart the dashboard and re-sync your repositories.

### Permission Errors When Closing Issues

If you see "Permission Denied" when trying to close issues on GitHub, your token needs the `repo` scope. Generate a new token at https://github.com/settings/tokens with full repository access.

### API Connection Issues

Check the Live Logs tab in the dashboard for detailed error messages, or view the log file directly:

```bash
tail -50 ~/.devin-sheriff/sheriff.log
```

## Requirements

- Python 3.8+
- GitHub Personal Access Token (with `repo` scope for full functionality)
- Devin API Key

## Disclaimer

This tool uses paid API credits from Devin AI. Each Scope and Execute operation consumes credits based on complexity. Monitor your usage to avoid unexpected charges.

Always review Pull Requests created by Devin before merging.

## License

This project is provided as-is for educational and development purposes.
