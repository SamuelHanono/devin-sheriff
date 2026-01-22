# Devin Sheriff (Local)

Devin Sheriff is a local CLI and Dashboard tool that connects to GitHub repositories and uses the Devin AI API to automatically **Scope** (plan) and **Execute** (fix) GitHub issues. It allows developers to manage issues locally, request AI-generated action plans, and trigger autonomous coding sessions that result in real Pull Requests.

## Features

**Secure Local Configuration** stores your API keys (GitHub PAT and Devin API Key) locally in `~/.devin-sheriff/config.json`, keeping your credentials safe and out of version control.

**Repo-Agnostic Design** works with any public or private GitHub repository. Simply connect a repo URL and start managing issues immediately.

**Two-Stage AI Workflow** provides a thoughtful approach to automated fixes. The **Scoping** phase analyzes an issue and returns a structured JSON Action Plan with confidence scores, files to change, and implementation steps. The **Execution** phase takes the approved plan and autonomously clones the repo, writes code, runs tests, and opens a Pull Request.

**Real-time Dashboard** built with Streamlit displays issue status (NEW, SCOPED, PR_OPEN), confidence scores, action plans, and direct links to created PRs.

**Resilient API Integration** includes polling logic to wait for Devin sessions to complete and robust error handling for API timeouts.

## Technical Architecture

Devin Sheriff is built with Python 3.x and uses the following technologies:

The **Database Layer** uses SQLite managed via SQLAlchemy for storing Repos, Issues, and Session logs locally at `~/.devin-sheriff/sheriff.db`.

The **Frontend** is a Streamlit-powered interactive Dashboard that provides a visual interface for managing issues and triggering AI workflows.

The **CLI** is built with Typer for command-line operations like setup and connecting repositories.

**API Integrations** include the GitHub API for fetching open issues and repository details, and the Devin API (v1) for two distinct session types: SCOPE sessions that analyze issues and return structured JSON Action Plans, and EXECUTE sessions that implement fixes and open Pull Requests.

## Project Structure

```
devin-sheriff/
├── main.py                    # CLI entrypoint
├── requirements.txt           # Python dependencies
├── devin_sheriff/
│   ├── __init__.py
│   ├── cli.py                 # Typer CLI commands (setup, connect)
│   ├── config.py              # Configuration management
│   ├── dashboard.py           # Streamlit Dashboard UI
│   ├── devin_client.py        # Devin API client
│   ├── github_client.py       # GitHub API client
│   └── models.py              # SQLAlchemy models (Repo, Issue, DevinSession)
└── ~/.devin-sheriff/          # Local config directory (created on setup)
    ├── config.json            # API keys
    └── sheriff.db             # SQLite database
```

## Prerequisites

Before installing Devin Sheriff, ensure you have the following:

- Python 3.8 or higher
- Git
- A GitHub Personal Access Token (PAT) with repo access
- A Devin API Key (obtain from your Devin account)

## Installation

Clone the repository and set up a virtual environment:

```bash
git clone https://github.com/SamuelHanono/devin-sheriff.git
cd devin-sheriff
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Run the interactive setup to configure your API keys:

```bash
python main.py setup
```

This will prompt you for your GitHub PAT and Devin API Key, verify the connections, and save the configuration to `~/.devin-sheriff/config.json`.

## Usage

### Connecting a Repository

Connect a GitHub repository to start tracking its issues:

```bash
python main.py connect https://github.com/owner/repo
```

This command fetches the repository details and all open issues, storing them in the local SQLite database.

### Running the Dashboard

Launch the Streamlit Dashboard to manage issues visually:

```bash
streamlit run devin_sheriff/dashboard.py
```

The Dashboard provides the following capabilities:

**Repository Selection** allows you to switch between connected repositories using the sidebar dropdown.

**Issue Overview** displays a table of all tracked issues with their status and confidence scores.

**Scope (Plan)** triggers the Devin AI to analyze an issue and generate an action plan. The AI clones the repository, examines the codebase, and returns a JSON response containing a summary, files to change, step-by-step action plan, and a confidence score (0-100).

**Execute (Fix)** takes the approved action plan and instructs Devin to implement the fix. The AI creates a branch, commits the changes, pushes to GitHub, and opens a Pull Request.

**Reset Issue** allows you to clear the scope and status of an issue to start fresh.

### Issue Status Workflow

Issues progress through the following statuses:

| Status | Description |
|--------|-------------|
| NEW | Issue has been fetched but not yet analyzed |
| SCOPED | Devin has analyzed the issue and generated an action plan |
| PR_OPEN | Devin has implemented the fix and opened a Pull Request |

## Configuration

All configuration is stored locally in `~/.devin-sheriff/config.json`:

```json
{
  "github_token": "ghp_xxxxxxxxxxxx",
  "devin_api_key": "apk_user_xxxxxxxxxxxx",
  "devin_api_url": "https://api.devin.ai/v1"
}
```

The database file `~/.devin-sheriff/sheriff.db` stores all repository and issue data locally.

## API Integration Details

### GitHub API

The GitHub client authenticates using a Personal Access Token and provides methods to verify authentication, fetch repository details, and retrieve open issues with pagination support.

### Devin API (v1)

The Devin client interacts with the official Devin API at `https://api.devin.ai/v1`. It supports two session types:

**Scope Sessions** send a prompt instructing Devin to analyze an issue and return a structured JSON response with the action plan. The client polls the session status until completion (default timeout: 5 minutes).

**Execute Sessions** send a prompt with the approved action plan, instructing Devin to implement the fix and open a PR. The client polls until completion (default timeout: 10 minutes).

## Disclaimer

This tool uses paid API credits from Devin AI. Each Scope and Execute operation consumes API credits based on the complexity and duration of the AI session. Please monitor your Devin API usage to avoid unexpected charges.

The quality of AI-generated fixes depends on the clarity of the issue description and the complexity of the codebase. Always review Pull Requests created by Devin before merging.

## License

This project is provided as-is for educational and development purposes.

## Contributing

Contributions are welcome! Please open an issue or submit a Pull Request with any improvements or bug fixes.
