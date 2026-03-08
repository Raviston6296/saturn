"""
Saniyan — Main entry point.

Run modes:
  1. Server mode (default): Start FastAPI webhook server + background worker
     $ python main.py

  2. CLI mode: Run a single task directly from the command line
     $ python main.py --task "Fix the login bug" --repo owner/repo

  3. Local mode: Run against the current directory (no clone)
     $ python main.py --local --task "Add tests for utils.py"
"""

from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Saniyan — Autonomous Coding Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start the webhook server (listens for Cliq messages)
  python main.py

  # Run a single task against a GitHub repo
  python main.py --task "Fix the failing tests" --repo Raviston6296/my-app

  # Run against the current directory
  python main.py --local --task "Refactor auth module to use async"

  # Dry run (no file writes)
  python main.py --local --task "Add error handling" --dry-run
        """,
    )

    parser.add_argument(
        "--task", "-t",
        type=str,
        default=None,
        help="Task description (CLI mode)",
    )
    parser.add_argument(
        "--repo", "-r",
        type=str,
        default=None,
        help="GitHub repo (owner/repo format)",
    )
    parser.add_argument(
        "--local", "-l",
        action="store_true",
        help="Run against current directory instead of cloning a repo",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write any files (preview mode)",
    )
    parser.add_argument(
        "--server", "-s",
        action="store_true",
        help="Start the webhook server (default if no --task given)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Server host (default: from config)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Server port (default: from config)",
    )

    args = parser.parse_args()

    if args.task:
        # ── CLI Mode: Run a single task ──
        _run_cli_task(args)
    else:
        # ── Server Mode: Start webhook server ──
        _run_server(args)


def _run_cli_task(args):
    """Run a single task from the command line."""
    from config import settings
    from agent.agent import AutonomousAgent

    task = args.task
    repo_name = args.repo or settings.github_default_repo

    if args.local:
        # Run against current directory
        import os
        workspace = os.getcwd()
        print(f"🤖 Saniyan — running locally in {workspace}")
    else:
        # Clone repo first
        if not repo_name:
            print("ERROR: Provide --repo owner/name or set GITHUB_DEFAULT_REPO, or use --local")
            sys.exit(1)

        from dispatcher.workspace import Workspace
        import uuid
        task_id = f"CLI-{uuid.uuid4().hex[:8].upper()}"
        ws = Workspace(
            task_id=task_id,
            repo_url=f"https://github.com/{repo_name}.git",
            branch_name=f"saniyan/cli-{uuid.uuid4().hex[:6]}",
        )
        workspace = str(ws.setup())
        print(f"📁 Cloned {repo_name} → {workspace}")

    agent = AutonomousAgent(
        workspace=workspace,
        repo_name=repo_name,
        branch_name="",
        dry_run=args.dry_run,
    )

    summary = agent.run(task)
    print(f"\n📝 SUMMARY:\n{summary}")


def _run_server(args):
    """Start the FastAPI webhook server."""
    import uvicorn
    from config import settings
    from server.app import create_app
    from utils.logging import setup_logging

    setup_logging()

    host = args.host or settings.server_host
    port = args.port or settings.server_port

    print(f"""
╔══════════════════════════════════════════════════╗
║  🤖 SANIYAN — Autonomous Coding Agent           ║
║  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ║
║  Webhook server starting...                      ║
║  POST /webhook/cliq → receives tasks from Cliq   ║
║  GET  /health       → health check               ║
║                                                  ║
║  Host: {host:<41s} ║
║  Port: {port:<41d} ║
╚══════════════════════════════════════════════════╝
""")

    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

