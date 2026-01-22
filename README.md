# Devin Sheriff

A production-ready dashboard that connects your GitHub repositories to Devin AI for automated issue triage, scoping, and resolution. Transform your issue backlog into actionable pull requests with AI-powered analysis and execution.

## What It Does

Devin Sheriff provides a streamlined two-stage workflow for handling GitHub issues across any of your repositories:

**Stage 1: Scoping** - Devin AI analyzes an issue, explores your codebase, and generates a structured action plan with confidence scores, risk assessment, and implementation steps.

**Stage 2: Execution** - With your approval, Devin AI implements the approved plan, writes code, runs tests, and opens a Pull Request automatically.

All data is stored locally on your machine. Your API keys never leave your system except when communicating directly with GitHub and Devin APIs.

## Key Features

**Mission Control Dashboard** - A 3-column layout providing high data density with clear readability. View your issue queue, detailed plans, and available actions at a glance.

**First-Run Wizard** - New users are greeted with a friendly welcome screen that guides them through connecting their first repository directly from the dashboard.

**Live Mission Log** - A real-time scrolling terminal view showing all system activity including syncing, scoping, and execution progress. Never wonder "is it stuck?" again.

**Auto-Healer** - Monitors CI status on created PRs. If tests fail, the Auto-Healer can automatically trigger a fix attempt (up to 3 retries) with context about what went wrong.

**Sheriff's Rules (Governance)** - Define coding standards, security policies, and team conventions that are automatically injected into every Devin prompt.

**Risk Assessment** - Each plan is analyzed for risk level based on the files being modified, helping you make informed decisions before execution.

## Installation

```bash
git clone https://github.com/SamuelHanono/devin-sheriff.git
cd devin-sheriff

python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

pip install -r requirements.txt
```

## Initial Setup

Configure your API credentials by running the interactive setup:

```bash
python main.py setup
```

You will be prompted for:

1. **GitHub Personal Access Token** - Generate one at https://github.com/settings/tokens with `repo` scope for full functionality (reading issues, creating PRs, closing issues).

2. **Devin API Key** - Obtain from your Devin AI account dashboard.

The setup wizard verifies both connections before saving.

## Connecting Your Own Projects

Devin Sheriff works with any GitHub repository you have access to, whether public or private. This is not limited to any specific repository.

**From the Dashboard (Recommended)**

1. Launch the dashboard: `streamlit run devin_sheriff/dashboard.py`
2. If no repositories are connected, you'll see the Welcome Screen
3. Paste your repository URL (e.g., `https://github.com/your-org/your-project`)
4. Click "Connect Repository"

**From the Command Line**

```bash
python main.py connect https://github.com/your-org/your-project
```

You can connect multiple repositories and switch between them using the sidebar dropdown.

## Happy Path Walkthrough

Here's the complete workflow from installation to merged PR:

**Step 1: Install and Configure**
```bash
git clone https://github.com/SamuelHanono/devin-sheriff.git
cd devin-sheriff
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python main.py setup
```

**Step 2: Connect Your Repository**
```bash
python main.py connect https://github.com/your-org/your-project
```

**Step 3: Launch the Dashboard**
```bash
streamlit run devin_sheriff/dashboard.py
```

**Step 4: Scope an Issue**
- Select your repository from the sidebar
- Choose an issue from the Issue Queue
- Click "Scope Issue" to have Devin analyze it
- Review the generated plan, confidence score, and risk level

**Step 5: Execute the Fix**
- If satisfied with the plan, click "Execute Fix"
- Devin will implement the changes and open a PR
- The PR link appears in the dashboard when complete

**Step 6: Review and Merge**
- Click the PR link to review on GitHub
- Check CI status directly from the dashboard
- If CI fails, use Auto-Healer for automatic fix attempts
- Merge when ready

## Issue Statuses

| Status | Description |
|--------|-------------|
| NEW | Issue synced from GitHub, not yet analyzed |
| SCOPED | Action plan generated, ready for execution |
| EXECUTING | Devin is currently implementing the fix |
| PR_OPEN | Pull Request created, awaiting review |
| DONE | Issue resolved and closed |

## Dashboard Tabs

**Mission Control** - The main workspace with 3-column layout showing issue queue, details, and actions.

**Sheriff's Rules** - Configure governance rules that are injected into all Devin prompts. Use this to enforce coding standards, security policies, or team conventions.

**Live Mission Log** - Real-time view of all system activity with auto-refresh capability.

## Local Storage

All data is stored in `~/.devin-sheriff/`:

| File | Purpose |
|------|---------|
| `config.json` | API credentials (GitHub PAT, Devin API Key, webhook URL) |
| `sheriff.db` | SQLite database (repositories, issues, session history) |
| `sheriff.log` | Application logs |
| `sheriff_rules.md` | Your governance rules |

## CLI Commands

```bash
python main.py setup          # Configure API credentials
python main.py connect <url>  # Connect a GitHub repository
python main.py list           # List connected repositories
python main.py sync [repo]    # Sync issues from GitHub
python main.py remove <repo>  # Disconnect a repository
python main.py patrol         # Auto-scope all NEW issues (batch mode)
```

## Troubleshooting

**"Repository not found" when connecting**

Verify the URL is correct and your GitHub token has access. For private repositories, ensure your token has `repo` scope.

**"Permission denied" when closing issues**

Your GitHub token needs write access. Generate a new token at https://github.com/settings/tokens with full `repo` scope.

**Dashboard shows empty after connecting a repo**

Click "Quick Sync" in the sidebar to fetch issues from GitHub. If the repository has no open issues, the dashboard will indicate this.

**Scoping or execution seems stuck**

Check the Live Mission Log tab for real-time activity. Operations typically take 30-60 seconds for scoping and 2-10 minutes for execution depending on complexity.

**Factory Reset**

To completely reset and start fresh:
1. Open the sidebar in the dashboard
2. Expand "Danger Zone"
3. Click "Delete All Data & Reset"
4. Confirm the action

Or from command line:
```bash
rm ~/.devin-sheriff/sheriff.db
```

## Requirements

- Python 3.8 or higher
- GitHub Personal Access Token (with `repo` scope for full functionality)
- Devin API Key

## Cost Considerations

This tool uses paid API credits from Devin AI. Each Scope and Execute operation consumes credits based on complexity. Monitor your Devin usage to manage costs effectively.

Always review Pull Requests created by Devin before merging into production branches.

## Security Notes

- API keys are stored locally and never transmitted to third parties
- All GitHub communication uses your personal access token
- Webhook notifications (if configured) only send issue numbers and titles, not code
- The database contains issue metadata, not source code

## License

This project is provided as-is for development and automation purposes.
