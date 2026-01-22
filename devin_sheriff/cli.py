import typer
import re
from typing import Optional
from .config import load_config, save_config, Config, ensure_config_dir
from .models import init_db, SessionLocal, Repo, Issue
from .sync import sync_repo_issues
from .github_client import GitHubClient
from .devin_client import DevinClient

# Initialize with help text
app = typer.Typer(help="🤠 Devin Sheriff CLI - Manage your AI Engineer locally.")

# --- HELPERS ---
def get_db():
    """Helper to get DB session."""
    return SessionLocal()

def print_success(msg: str):
    typer.echo(typer.style(f"✓ {msg}", fg=typer.colors.GREEN))

def print_warning(msg: str):
    typer.echo(typer.style(f"ℹ {msg}", fg=typer.colors.YELLOW))

def print_error(msg: str):
    typer.echo(typer.style(f"✗ {msg}", fg=typer.colors.RED, bold=True))

# --- COMMANDS ---

@app.command()
def setup():
    """
    Interactive setup to store your API keys securely.
    """
    ensure_config_dir()
    init_db()
    
    typer.echo(typer.style("🤠 Devin Sheriff Setup", fg=typer.colors.CYAN, bold=True))
    typer.echo("Keys are stored locally in ~/.devin-sheriff/config.json\n")
    
    # Load existing or create new
    try:
        current = load_config()
        gh_token = current.github_token
        devin_key = current.devin_api_key
    except:
        gh_token = ""
        devin_key = ""

    new_gh = typer.prompt("Enter GitHub PAT", default=gh_token, hide_input=True)
    new_devin = typer.prompt("Enter Devin API Key", default=devin_key, hide_input=True)

    # Save
    save_config(Config(github_token=new_gh, devin_api_key=new_devin))
    print_success("Configuration saved.")
    
    # Verify immediately
    typer.echo("\nVerifying connections...")
    config = load_config()
    
    # 1. Verify GitHub
    try:
        gh = GitHubClient(config)
        user = gh.verify_auth()
        print_success(f"GitHub Connected: {user}")
    except Exception as e:
        print_error(f"GitHub Verification Failed: {e}")

    # 2. Verify Devin
    try:
        devin = DevinClient(config)
        if devin.verify_auth():
            print_success("Devin API Connected")
        else:
            print_error("Devin API Key Invalid")
    except Exception as e:
        print_error(f"Devin Check Failed: {e}")


@app.command()
def connect(url: str):
    """
    Connect a GitHub repository to the dashboard.
    Example: python main.py connect https://github.com/owner/repo
    """
    config = load_config()
    if not config.github_token:
        print_error("Configuration missing. Run 'python main.py setup' first.")
        raise typer.Exit(code=1)

    # Regex to parse owner/repo
    match = re.search(r"github\.com/([^/]+)/([^/]+)", url)
    if not match:
        print_error("Invalid GitHub URL. Must contain 'github.com/owner/repo'.")
        raise typer.Exit(code=1)

    owner, repo_name = match.groups()
    repo_name = repo_name.replace(".git", "") # Clean .git extension

    db = get_db()
    try:
        # Check for duplicates
        existing = db.query(Repo).filter(Repo.owner == owner, Repo.name == repo_name).first()
        if existing:
            print_warning(f"Repo '{owner}/{repo_name}' is already connected.")
            if typer.confirm("Do you want to re-sync issues now?"):
                msg = sync_repo_issues(existing.url)
                print_success(msg)
            return

        # Add new repo
        repo = Repo(url=url, owner=owner, name=repo_name)
        db.add(repo)
        db.commit()
        print_success(f"Repo '{repo_name}' added to database.")
        
        # Auto-Sync
        typer.echo("Fetching open issues...")
        msg = sync_repo_issues(url)
        print_success(msg)

    finally:
        db.close()


@app.command("list")
def list_repos():
    """
    List all connected repositories and their issue counts.
    """
    db = get_db()
    try:
        repos = db.query(Repo).all()
        if not repos:
            print_warning("No repositories connected yet.")
            return

        typer.echo(f"{'ID':<4} | {'Repository':<30} | {'Issues (Open)':<10}")
        typer.echo("-" * 50)
        
        for r in repos:
            # Count open issues
            count = db.query(Issue).filter(Issue.repo_id == r.id, Issue.state == "open").count()
            typer.echo(f"{r.id:<4} | {r.owner}/{r.name:<25} | {count:<10}")
            
    finally:
        db.close()


@app.command()
def sync(repo_name: Optional[str] = typer.Argument(None)):
    """
    Sync issues from GitHub.
    Usage: 'python main.py sync' (Syncs ALL) or 'python main.py sync repo-name'
    """
    db = get_db()
    try:
        repos = db.query(Repo).all()
        if not repos:
            print_warning("No repos to sync.")
            return

        target_repos = repos
        if repo_name:
            target_repos = [r for r in repos if r.name == repo_name]
            if not target_repos:
                print_error(f"Repository '{repo_name}' not found.")
                return

        for r in target_repos:
            typer.echo(f"Syncing {r.owner}/{r.name}...")
            msg = sync_repo_issues(r.url)
            print_success(f"{r.name}: {msg}")

    finally:
        db.close()


@app.command()
def remove(repo_name: str):
    """
    Disconnect a repository and delete its local history.
    """
    db = get_db()
    try:
        repo = db.query(Repo).filter(Repo.name == repo_name).first()
        if not repo:
            print_error(f"Repository '{repo_name}' not found.")
            return

        confirm = typer.confirm(f"Are you sure you want to remove '{repo.name}' and all its local history?", default=False)
        if not confirm:
            typer.echo("Aborted.")
            return

        # Cascade delete (SQLAlchemy usually handles this, but let's be explicit if needed)
        db.query(Issue).filter(Issue.repo_id == repo.id).delete()
        db.delete(repo)
        db.commit()
        
        print_success(f"Repository '{repo_name}' removed.")
    
    except Exception as e:
        print_error(f"Error removing repo: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    app()