"""
RepoManager — persistent bare clone + git worktrees for task isolation.

Like Stripe's Minions: one Saturn instance deeply understands one repo.
Each task gets a lightweight git worktree (not a full clone).

Flow:
  1. On startup: clone repo as bare (or fetch if already exists)
  2. On task: `git worktree add` → cheap isolated workspace per branch
  3. Agent works inside the worktree
  4. On completion: `git worktree remove` → clean up
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from config import settings


class RepoManager:
    """
    Manages a persistent bare clone of the target repo
    and creates/removes git worktrees for each task.
    """

    def __init__(
        self,
        repo_url: str = "",
        repo_local_path: str = "",
        worktree_base_dir: str = "",
    ):
        self.repo_url = repo_url or settings.repo_url
        self.repo_path = Path(repo_local_path or settings.repo_local_path)
        self.worktree_base = Path(worktree_base_dir or settings.worktree_base_dir)

        # Track active worktrees for cleanup
        self._active_worktrees: dict[str, str] = {}  # task_id → branch_name

    # ━━━ Startup ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def ensure_repo(self):
        """
        Ensure the bare clone exists and is up-to-date.
        Called once at startup.

        For internal GitLab, auto-injects the token into the clone URL
        if GITLAB_TOKEN is configured (so no manual credential setup needed).
        """
        if not self.repo_url:
            raise RuntimeError(
                "REPO_URL not configured. Saturn needs a repo to watch. "
                "Set REPO_URL in .env (e.g. https://gitlab.yourcompany.com/group/repo.git)"
            )

        clone_url = self._auth_url(self.repo_url)

        if self.repo_path.exists() and (self.repo_path / "HEAD").exists():
            # Already cloned — update the remote URL (in case token changed) and fetch
            print(f"📦 Repo exists at {self.repo_path}, fetching updates...")
            self._run_in_repo(f"git remote set-url origin {clone_url}")
            self._run_in_repo("git fetch --all --prune")
            self._cleanup_stale_worktrees()
        else:
            # First time — bare clone
            print(f"📥 Cloning {self.repo_url} (bare) → {self.repo_path}...")
            self.repo_path.parent.mkdir(parents=True, exist_ok=True)
            self._run(f"git clone --bare {clone_url} {self.repo_path}")

        # Configure git identity on the bare repo
        self._run_in_repo("git config user.name 'Saturn Bot'")
        self._run_in_repo("git config user.email 'saturn@bot.dev'")
        self._run_in_repo("git config http.sslVerify false")

        # Ensure worktree base dir exists
        self.worktree_base.mkdir(parents=True, exist_ok=True)

        print(f"✅ Repo ready: {self.repo_url}")
        print(f"   Bare clone: {self.repo_path}")
        print(f"   Worktrees:  {self.worktree_base}")

    # ━━━ Worktree management ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def refresh(self):
        """Fetch latest from origin. Call before each new task."""
        self._run_in_repo("git fetch --all --prune")

    def create_worktree(self, task_id: str, branch_name: str) -> Path:
        """
        Create a git worktree for a task.
        Returns the path to the worktree directory.

        This is FAST — no network clone, just a local checkout.
        """
        worktree_path = self.worktree_base / task_id

        # Clean up if leftover from a crashed run
        if worktree_path.exists():
            self._force_remove_worktree(task_id)

        # Safety: generate a branch name if empty
        if not branch_name or not branch_name.strip():
            branch_name = f"saturn/task/{task_id.lower()}"

        # Determine the base ref (origin/main or origin/master)
        base_ref = self._get_default_branch()

        # If branch already exists, clean up any stale worktree using it first
        existing = self._run_in_repo("git branch --list " + branch_name)
        if existing.strip():
            self._remove_worktree_for_branch(branch_name)
            try:
                self._run_in_repo(f"git branch -D {branch_name}")
            except RuntimeError as e:
                print(f"  ⚠️ Could not delete old branch '{branch_name}': {e}")
                # Force-prune and retry once
                self._run_in_repo("git worktree prune")
                try:
                    self._run_in_repo(f"git branch -D {branch_name}")
                except RuntimeError:
                    print(f"  ⚠️ Branch still locked — will use -B to force-reset")

        # Create the worktree with a new branch from the base (-B force-resets if branch still exists)
        self._run_in_repo(
            f"git worktree add {worktree_path} -B {branch_name} {base_ref}"
        )

        # Configure git identity inside the worktree
        self._run_in_worktree(worktree_path, "git config user.name 'Saturn Bot'")
        self._run_in_worktree(worktree_path, "git config user.email 'saturn@bot.dev'")

        self._active_worktrees[task_id] = branch_name

        print(f"🌿 Worktree created: {worktree_path} (branch: {branch_name})")
        return worktree_path

    def remove_worktree(self, task_id: str):
        """Remove a worktree after task completion."""
        worktree_path = self.worktree_base / task_id
        branch_name = self._active_worktrees.pop(task_id, None)

        try:
            if worktree_path.exists():
                self._run_in_repo(f"git worktree remove {worktree_path} --force")
        except RuntimeError:
            # Force cleanup if git worktree remove fails
            self._force_remove_worktree(task_id)

        # Prune stale worktree refs
        self._run_in_repo("git worktree prune")

        print(f"🧹 Worktree removed: {task_id}")

    # ━━━ Repo info (for context building) ━━━━━━━━━━━━━━━━━━━━━━━

    def get_branches(self) -> str:
        """List remote branches."""
        return self._run_in_repo("git branch -r --format='%(refname:short)'")

    def get_recent_commits(self, count: int = 20) -> str:
        """Get recent commits from the default branch."""
        base = self._get_default_branch()
        return self._run_in_repo(f"git log {base} --oneline -{count}")

    def get_tags(self, count: int = 10) -> str:
        """Get recent tags."""
        return self._run_in_repo(
            f"git tag --sort=-creatordate | head -{count}"
        )

    def get_contributors(self, count: int = 10) -> str:
        """Get top contributors."""
        base = self._get_default_branch()
        return self._run_in_repo(
            f"git shortlog -sn {base} | head -{count}"
        )

    def get_file_tree(self) -> str:
        """Get the full file tree from HEAD (no checkout needed — reads from bare)."""
        base = self._get_default_branch()
        return self._run_in_repo(f"git ls-tree -r --name-only {base} | head -200")

    # ━━━ Internal helpers ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _get_default_branch(self) -> str:
        """Detect whether the repo uses main or master."""
        try:
            refs = self._run_in_repo("git branch -r --format='%(refname:short)'")
            if "origin/main" in refs:
                return "origin/main"
            if "origin/master" in refs:
                return "origin/master"
        except RuntimeError:
            pass
        return "origin/main"

    def _cleanup_stale_worktrees(self):
        """Prune stale worktrees and clean leftover directories from crashes."""
        self._run_in_repo("git worktree prune")

        # Remove orphan directories in worktree base
        if self.worktree_base.exists():
            worktree_list = self._run_in_repo("git worktree list --porcelain")
            active_paths = set()
            for line in worktree_list.split("\n"):
                if line.startswith("worktree "):
                    active_paths.add(line.split(" ", 1)[1].strip())

            for entry in self.worktree_base.iterdir():
                if entry.is_dir() and str(entry.resolve()) not in active_paths:
                    print(f"🧹 Cleaning orphan worktree dir: {entry.name}")
                    shutil.rmtree(entry, ignore_errors=True)

    def _remove_worktree_for_branch(self, branch_name: str):
        """Find and remove any stale worktree that has `branch_name` checked out."""
        try:
            worktree_list = self._run_in_repo("git worktree list --porcelain")
        except RuntimeError:
            return

        # Parse porcelain output: blocks separated by blank lines
        # Each block has "worktree <path>", "HEAD <sha>", "branch refs/heads/<name>"
        current_path = None
        for line in worktree_list.split("\n"):
            if line.startswith("worktree "):
                current_path = line.split(" ", 1)[1].strip()
            elif line.startswith("branch ") and current_path:
                ref = line.split(" ", 1)[1].strip()  # e.g. refs/heads/saturn/task/foo
                wt_branch = ref.replace("refs/heads/", "")
                if wt_branch == branch_name:
                    print(f"  🧹 Removing stale worktree for branch '{branch_name}': {current_path}")
                    stale_path = Path(current_path)
                    if stale_path.exists():
                        shutil.rmtree(stale_path, ignore_errors=True)
                    try:
                        self._run_in_repo("git worktree prune")
                    except RuntimeError:
                        pass
                    # Also remove from active tracking
                    stale_task_id = stale_path.name
                    self._active_worktrees.pop(stale_task_id, None)
                    return

    def _force_remove_worktree(self, task_id: str):
        """Force-remove a worktree directory."""
        worktree_path = self.worktree_base / task_id
        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)
        try:
            self._run_in_repo("git worktree prune")
        except RuntimeError:
            pass

    def _git_env(self) -> dict[str, str]:
        """Build env for git subprocesses — disables SSL verify for internal GitLab."""
        import os
        env = os.environ.copy()
        env["GIT_SSL_NO_VERIFY"] = "1"
        return env

    def _run(self, cmd: str) -> str:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=120,
            env=self._git_env(),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed: {cmd}\nstderr: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def _run_in_repo(self, cmd: str) -> str:
        result = subprocess.run(
            cmd, shell=True, cwd=self.repo_path,
            capture_output=True, text=True, timeout=120,
            env=self._git_env(),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Repo command failed: {cmd}\nstderr: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def _run_in_worktree(self, worktree_path: Path, cmd: str) -> str:
        result = subprocess.run(
            cmd, shell=True, cwd=worktree_path,
            capture_output=True, text=True, timeout=120,
            env=self._git_env(),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Worktree command failed: {cmd}\nstderr: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def _auth_url(self, url: str) -> str:
        """
        Inject GitLab token into HTTPS clone URL for authentication.

        https://gitlab.company.com/group/repo.git
        → https://oauth2:glpat-xxx@gitlab.company.com/group/repo.git

        If the URL already has credentials, or if no token is configured,
        returns the URL as-is.
        """
        if not url.startswith("https://"):
            return url  # SSH or other protocol — no injection
        if "@" in url.split("//")[1]:
            return url  # Already has credentials
        if not settings.gitlab_token:
            return url  # No token configured

        # Inject oauth2:token@ after https://
        return url.replace("https://", f"https://oauth2:{settings.gitlab_token}@", 1)

