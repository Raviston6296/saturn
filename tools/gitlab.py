"""
GitLab tools — create Merge Requests, read issues via python-gitlab.
"""

from __future__ import annotations

import subprocess
import gitlab

from config import settings


class GitLabTools:
    """GitLab API operations — MR creation, issue reading."""

    def __init__(self, project_id: str = "", workspace: str = ""):
        self.project_id = project_id or settings.gitlab_project_id
        self.workspace = workspace or "."
        self._gitlab: gitlab.Gitlab | None = None
        self._project = None

    @property
    def gl(self) -> gitlab.Gitlab:
        if self._gitlab is None:
            if not settings.gitlab_url:
                raise RuntimeError("GITLAB_URL not configured")
            if not settings.gitlab_token:
                raise RuntimeError("GITLAB_TOKEN not configured")
            self._gitlab = gitlab.Gitlab(
                url=settings.gitlab_url,
                private_token=settings.gitlab_token,
            )
        return self._gitlab

    @property
    def project(self):
        if self._project is None:
            if not self.project_id:
                raise RuntimeError("GITLAB_PROJECT_ID not configured")
            self._project = self.gl.projects.get(self.project_id)
        return self._project

    def create_merge_request(
        self,
        title: str,
        body: str,
        base_branch: str = "",
    ) -> str:
        """Create a GitLab Merge Request."""
        try:
            base_branch = base_branch or settings.gitlab_default_branch

            # Get current branch from git (the source branch)
            result = subprocess.run(
                "git rev-parse --abbrev-ref HEAD",
                shell=True, capture_output=True, text=True,
                cwd=self.workspace,
            )
            source_branch = result.stdout.strip()

            if not source_branch or source_branch == base_branch:
                return "ERROR: Cannot create MR — source branch is same as target, or not on a branch."

            mr = self.project.mergerequests.create({
                "source_branch": source_branch,
                "target_branch": base_branch,
                "title": title,
                "description": self._format_mr_body(body),
                "remove_source_branch": True,
            })

            # Add saturn label if possible
            try:
                mr.labels = (mr.labels or []) + ["saturn-auto"]
                mr.save()
            except Exception:
                pass

            return (
                f"OK: MR created → {mr.web_url}\n"
                f"Title: {mr.title}\n"
                f"Number: !{mr.iid}"
            )

        except gitlab.exceptions.GitlabCreateError as e:
            return f"ERROR: GitLab API error: {e.error_message}"
        except Exception as e:
            return f"ERROR: Failed to create MR: {e}"

    def read_issue(self, issue_number: int) -> str:
        """Read a GitLab issue by number."""
        try:
            issue = self.project.issues.get(issue_number)
            notes = list(issue.notes.list(per_page=10))
            comment_text = ""
            if notes:
                comment_text = "\n\nCOMMENTS:\n" + "\n---\n".join(
                    f"@{n.author['username']} ({n.created_at}): {n.body[:500]}"
                    for n in notes[:10]
                )

            labels = ", ".join(issue.labels) if issue.labels else "(none)"

            return (
                f"ISSUE #{issue.iid}: {issue.title}\n"
                f"State: {issue.state} | Author: @{issue.author['username']}\n"
                f"Labels: {labels}\n"
                f"Created: {issue.created_at}\n\n"
                f"BODY:\n{issue.description or '(empty)'}"
                f"{comment_text}"
            )
        except Exception as e:
            return f"ERROR: Could not read issue #{issue_number}: {e}"

    def _format_mr_body(self, body: str) -> str:
        """Add Saturn footer to MR body."""
        return (
            f"{body}\n\n"
            f"---\n"
            f"🪐 *This MR was created automatically by Saturn "
            f"— an autonomous coding agent.*"
        )


# Keep backward-compatible alias
GitHubTools = GitLabTools

