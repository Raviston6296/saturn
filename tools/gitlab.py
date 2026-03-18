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
        """Create a GitLab Merge Request, or update an existing one for the same branch."""
        try:
            base_branch = base_branch or settings.gitlab_default_branch

            result = subprocess.run(
                "git rev-parse --abbrev-ref HEAD",
                shell=True, capture_output=True, text=True,
                cwd=self.workspace,
            )
            source_branch = result.stdout.strip()

            if not source_branch or source_branch == base_branch:
                return "ERROR: Cannot create MR — source branch is same as target, or not on a branch."

            try:
                mr = self.project.mergerequests.create({
                    "source_branch": source_branch,
                    "target_branch": base_branch,
                    "title": title,
                    "description": self._format_mr_body(body),
                    "remove_source_branch": True,
                })
            except gitlab.exceptions.GitlabCreateError as e:
                err_msg = str(e.error_message)
                if "already exists" in err_msg:
                    return self._update_existing_mr(
                        source_branch, base_branch, title, body,
                    )
                raise

            self._apply_label(mr)

            return (
                f"OK: MR created → {mr.web_url}\n"
                f"Title: {mr.title}\n"
                f"Number: !{mr.iid}"
            )

        except gitlab.exceptions.GitlabCreateError as e:
            return f"ERROR: GitLab API error: {e.error_message}"
        except Exception as e:
            return f"ERROR: Failed to create MR: {e}"

    def _update_existing_mr(
        self,
        source_branch: str,
        target_branch: str,
        title: str,
        body: str,
    ) -> str:
        """Find the open MR for *source_branch* and update its title/description."""
        mrs = self.project.mergerequests.list(
            source_branch=source_branch,
            target_branch=target_branch,
            state="opened",
            per_page=1,
        )
        if not mrs:
            return (
                "ERROR: MR already exists but could not be found via API. "
                f"Source branch: {source_branch}"
            )

        mr = mrs[0]
        mr.title = title
        mr.description = self._format_mr_body(body)
        mr.save()
        self._apply_label(mr)

        return (
            f"OK: MR updated → {mr.web_url}\n"
            f"Title: {mr.title}\n"
            f"Number: !{mr.iid}"
        )

    @staticmethod
    def _apply_label(mr) -> None:
        """Best-effort: add the saturn-auto label."""
        try:
            if "saturn-auto" not in (mr.labels or []):
                mr.labels = (mr.labels or []) + ["saturn-auto"]
                mr.save()
        except Exception:
            pass

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

