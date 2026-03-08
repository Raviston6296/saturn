"""
GitHub tools — create PRs, read issues via PyGithub.
"""

from __future__ import annotations

from github import Github, GithubException

from config import settings


class GitHubTools:
    """GitHub API operations — PR creation, issue reading."""

    def __init__(self, repo_name: str = ""):
        self.repo_name = repo_name or settings.github_default_repo
        self._github: Github | None = None
        self._repo = None

    @property
    def github(self) -> Github:
        if self._github is None:
            if not settings.github_token:
                raise RuntimeError("GITHUB_TOKEN not configured")
            self._github = Github(settings.github_token)
        return self._github

    @property
    def repo(self):
        if self._repo is None:
            if not self.repo_name:
                raise RuntimeError("No repo name configured")
            self._repo = self.github.get_repo(self.repo_name)
        return self._repo

    def create_pull_request(
        self,
        title: str,
        body: str,
        base_branch: str = "main",
    ) -> str:
        """Create a GitHub Pull Request."""
        try:
            # Get current branch from git (the head branch)
            import subprocess
            result = subprocess.run(
                "git rev-parse --abbrev-ref HEAD",
                shell=True, capture_output=True, text=True,
            )
            head_branch = result.stdout.strip()

            if not head_branch or head_branch == base_branch:
                return "ERROR: Cannot create PR — head branch is same as base, or not on a branch."

            pr = self.repo.create_pull(
                title=title,
                body=self._format_pr_body(body),
                head=head_branch,
                base=base_branch,
            )

            # Add the saturn label if it exists
            try:
                pr.add_to_labels("saturn-auto")
            except GithubException:
                pass  # Label doesn't exist, that's fine

            return f"OK: PR created → {pr.html_url}\nTitle: {pr.title}\nNumber: #{pr.number}"

        except GithubException as e:
            return f"ERROR: GitHub API error: {e.data.get('message', str(e))}"
        except Exception as e:
            return f"ERROR: Failed to create PR: {e}"

    def read_issue(self, issue_number: int) -> str:
        """Read a GitHub issue by number."""
        try:
            issue = self.repo.get_issue(number=issue_number)
            comments = list(issue.get_comments())
            comment_text = ""
            if comments:
                comment_text = "\n\nCOMMENTS:\n" + "\n---\n".join(
                    f"@{c.user.login} ({c.created_at}): {c.body[:500]}"
                    for c in comments[:10]
                )

            return (
                f"ISSUE #{issue.number}: {issue.title}\n"
                f"State: {issue.state} | Author: @{issue.user.login}\n"
                f"Labels: {', '.join(l.name for l in issue.labels)}\n"
                f"Created: {issue.created_at}\n\n"
                f"BODY:\n{issue.body or '(empty)'}"
                f"{comment_text}"
            )
        except GithubException as e:
            return f"ERROR: Could not read issue #{issue_number}: {e}"

    def _format_pr_body(self, body: str) -> str:
        """Add Saturn footer to PR body."""
        return (
            f"{body}\n\n"
            f"---\n"
            f"🤖 *This PR was created automatically by [Saturn](https://github.com/Raviston6296/saturn) "
            f"— an autonomous coding agent.*"
        )

