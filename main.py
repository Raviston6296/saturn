"""
Saturn — Main entry point.

Run modes:
  1. Server mode (default): Start FastAPI webhook server + background worker
     $ python main.py

  2. CLI mode: Run a single task directly from the command line
     $ python main.py --task "Fix the login bug"

  3. Local mode: Run against the current directory (no repo/worktree)
     $ python main.py --local --task "Add tests for utils.py"
"""

from __future__ import annotations

import argparse
import io
import sys


def main():
    # Force line-buffered stdout so prints appear in app.log immediately
    # (Python uses full buffering when stdout is redirected to a file).
    if not sys.stdout.isatty():
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding=sys.stdout.encoding,
            errors=sys.stdout.errors, line_buffering=True,
        )
    parser = argparse.ArgumentParser(
        description="Saturn — Autonomous Coding Agent (one instance per repo)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start the webhook server (listens for Cliq messages)
  python main.py

  # Run a single task (uses repo from REPO_URL in .env)
  python main.py --task "Fix the failing tests"

  # Run against the current directory (no worktree)
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
        "--local", "-l",
        action="store_true",
        help="Run against current directory instead of using a worktree",
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
        _run_cli_task(args)
    else:
        _run_server(args)


def _run_cli_task(args):
    """Run a single task from the command line."""
    from config import settings
    from agent.agent import AutonomousAgent
    from dpaas import ensure_dpaas_ready

    # One-time DPAAS initialisation (idempotent — skipped if already done)
    ensure_dpaas_ready()

    task = args.task

    if args.local:
        # Run against current directory — no worktree, no repo manager
        import os
        workspace = os.getcwd()
        print(f"🪐 Saturn — running locally in {workspace}")
        agent = AutonomousAgent(
            workspace=workspace,
            repo_name=settings.gitlab_project_id,
            dry_run=args.dry_run,
        )
    else:
        # Use worktree from the persistent bare clone
        if not settings.repo_url:
            print("ERROR: Set REPO_URL in .env, or use --local mode")
            sys.exit(1)

        from dispatcher.workspace import RepoManager
        import uuid

        repo_manager = RepoManager()
        repo_manager.ensure_repo()

        task_id = f"CLI-{uuid.uuid4().hex[:8].upper()}"
        branch_name = f"saturn/cli-{uuid.uuid4().hex[:6]}"
        worktree_path = repo_manager.create_worktree(task_id, branch_name)

        print(f"🌿 Worktree: {worktree_path} (branch: {branch_name})")

        agent = AutonomousAgent(
            workspace=str(worktree_path),
            repo_name=settings.gitlab_project_id,
            branch_name=branch_name,
            dry_run=args.dry_run,
            repo_manager=repo_manager,
        )

    summary = agent.run(task)
    print(f"\n📝 SUMMARY:\n{summary}")

    # Clean up worktree (not in local mode)
    if not args.local and 'repo_manager' in dir():
        try:
            repo_manager.remove_worktree(task_id)
        except Exception:
            pass


def _run_server(args):
    """Start the FastAPI webhook server."""
    import uvicorn
    from config import settings
    from server.app import create_app
    from utils.logging import setup_logging

    setup_logging()

    host = args.host or settings.server_host
    port = args.port or settings.server_port

    repo_display = settings.repo_url or "(not configured)"

    print(f"""
╔══════════════════════════════════════════════════════╗
║  🪐 SATURN — Autonomous Coding Agent                ║
║  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ║
║  One instance per repo · git worktrees · persistent  ║
║                                                      ║
║  POST /webhook/cliq → receives tasks from Cliq       ║
║  GET  /health       → health check                   ║
║                                                      ║
║  Repo:  {repo_display:<43s} ║
║  Host:  {host:<43s} ║
║  Port:  {port:<43d} ║
╚══════════════════════════════════════════════════════╝
""")

    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

