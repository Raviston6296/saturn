"""
Saniyan configuration — loaded from environment variables / .env file.
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

    # ── GitHub ──
    github_token: str = ""
    github_default_repo: str = ""

    # ── Zoho Cliq ──
    cliq_webhook_token: str = ""
    cliq_bot_api_url: str = ""
    cliq_auth_token: str = ""

    # ── Server ──
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # ── Agent ──
    max_loop_iterations: int = 20
    workspace_base_dir: str = "/tmp/saniyan-workspaces"

    @property
    def workspace_path(self) -> Path:
        p = Path(self.workspace_base_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()

