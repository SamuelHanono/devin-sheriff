import typer
import re
from rich.console import Console
from rich.panel import Panel
from .config import load_config, save_config, AppConfig
from .github_client import GitHubClient
from .devin_client import DevinClient
from .models import SessionLocal, Repo, Issue

app = typer.Typer()
console = Console()

@app.command()
def setup():
    """
    Interactive setup to configure API keys.
    """
    console.print(Panel.fit("Devin Sheriff (Local) Setup", style="bold blue"))

    current_config = load_config()

    # 1. GitHub Token
    github_token = typer.prompt(
        "Enter GitHub PAT (Personal Access Token)", 
        default=current_config.github_token or "", 
        hide_input=False 
    )
    
    # 2. Devin API Key
    devin_key = typer.prompt(
        "Enter Devin API Key", 
        default=current_config.devin_api_key or "", 
        hide_input=False
    )

    # Save
    new_config = AppConfig(github_token=github_token, devin_api_key=devin_key)
    save_config(new_config)
    console.print("[green]✓ Configuration saved to ~/.devin-sheriff/config.json[/green]")

    # Verify GitHub
    console.print("\n[yellow]Verifying GitHub connection...[/yellow]")
    try:
        gh_client = GitHubClient(new_config)
        user = gh_client.verify_auth()
        console.print(f"[bold green]✓ GitHub Connected as: {user}[/bold green]")
    except Exception as e:
        console.print(f"[bold red]✗ GitHub Failed:[/bold red] {e}")

    # Verify Devin
    console.print("\n[yellow]Verifying Devin connection...[/yellow]")
    try:
        dev_client = DevinClient(new_config)
        dev_client.verify_auth()
        console.print("[bold green]✓ Devin API Key Stored[/bold green]")
    except Exception as e:
        console.print(f"[bold red]✗ Devin Failed:[/bold red] {e}")

@app.command()
def connect(repo_url: str):
    """
    Connect to a GitHub repo and fetch open issues.
    Usage: python main.py connect https://github.com/owner/name
    """
    config = load_config()
    if not config.github_token:
        console.print("[red]Not authenticated. Run 'setup' first.[/red]")
        return

    # Parse owner/name from URL
    match = re.search(r"github\.com/([^/]+)/([^/]+)", repo_url)
    if not match:
        console.print("[red]Invalid GitHub URL. Must be https://github.com/owner/repo[/red]")
        return
    
    owner, repo_name = match.groups()
    repo_name = repo_name.replace(".git", "") # handle .git extension if present

    console.print(f"[yellow]Connecting to {owner}/{repo_name}...[/yellow]")
    
    try:
        gh = GitHubClient(config)
        
        # 1. Get Repo Info
        repo_data = gh.get_repo_details(owner, repo_name)
        
        # 2. Save Repo to DB
        db = SessionLocal()
        repo = db.query(Repo).filter_by(url=repo_url).first()
        if not repo:
            repo = Repo(
                owner=owner, 
                name=repo_name, 
                url=repo_url,
                default_branch=repo_data.get("default_branch", "main")
            )
            db.add(repo)
            db.commit()
            console.print(f"[green]✓ Repo '{repo_name}' added to database.[/green]")
        else:
            console.print(f"[blue]ℹ Repo '{repo_name}' already tracked.[/blue]")

        # 3. Fetch Issues
        console.print("[yellow]Fetching open issues...[/yellow]")
        issues_data = gh.fetch_open_issues(owner, repo_name)
        
        new_count = 0
        for i_data in issues_data:
            # Check if issue exists
            exists = db.query(Issue).filter_by(repo_id=repo.id, number=i_data["number"]).first()
            if not exists:
                new_issue = Issue(
                    repo_id=repo.id,
                    number=i_data["number"],
                    title=i_data["title"],
                    body=i_data.get("body", ""),
                    state=i_data["state"],
                    status="NEW"
                )
                db.add(new_issue)
                new_count += 1
        
        db.commit()
        console.print(f"[bold green]✓ Synced {len(issues_data)} open issues ({new_count} new).[/bold green]")
        db.close()

    except Exception as e:
        console.print(f"[bold red]Error connecting to repo:[/bold red] {e}")

@app.command()
def check():
    """Quickly check connection status."""
    setup()