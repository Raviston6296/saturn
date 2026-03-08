"""
Saturn configuration — loaded from environment variables / .env file.
"""

from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Anthropic ──
    anthropic_api_key: str = ""
    model_name: str = "claude-sonnet-4-20250514"
    thinking_budget_tokens: int = 10_000

    # ── GitLab ──
    gitlab_url: str = ""                    # e.g. https://gitlab.yourcompany.com
    gitlab_token: str = ""                  # Personal/Project access token
    gitlab_project_id: str = ""             # Numeric project ID or "group/project" path
    gitlab_default_branch: str = "main"     # Default branch name

    # ── Zoho Cliq ──
    cliq_webhook_token: str = ""
    cliq_bot_api_url: str = ""
    cliq_auth_token: str = ""

    # ── Server ──
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # ── Agent (per-repo instance) ──
    max_loop_iterations: int = 20

    # The single repo this Saturn instance watches
    repo_url: str = ""                      # e.g. https://gitlab.yourcompany.com/group/repo.git
    repo_local_path: str = "/data/saturn/repo"
    worktree_base_dir: str = "/data/saturn/tasks"

    @property
    def repo_path(self) -> Path:
        p = Path(self.repo_local_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def worktree_path(self) -> Path:
        p = Path(self.worktree_base_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()

