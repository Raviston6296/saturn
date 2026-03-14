"""
Saturn configuration — loaded from environment variables / .env file.
"""

from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="saturn.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Cursor CLI (replaces Ollama / Anthropic — all coding done by Cursor) ──
    cursor_cli_path: str = "agent"            # Cursor Agent CLI binary (install: curl https://cursor.com/install -fsS | bash)
    cursor_timeout_seconds: int = 600         # Max time per Cursor invocation (10 min)

    # ── Goose CLI (open-source AI coding agent by Block) ──
    goose_cli_path: str = "goose"             # Goose binary (install: see agent/goose_cli.py)
    goose_timeout_seconds: int = 600          # Max time per Goose invocation (10 min)
    goose_provider: str = ""                  # e.g. "anthropic", "openai", "ollama" (sets GOOSE_PROVIDER)
    goose_model: str = ""                     # e.g. "claude-3-5-sonnet-20241022" (sets GOOSE_MODEL)

    # ── Legacy LLM (kept for backward compat, not used when Cursor/Goose is primary) ──
    anthropic_api_key: str = ""
    model_name: str = "claude-sonnet-4-20250514"
    thinking_budget_tokens: int = 10_000
    llm_provider: str = "cursor"              # "cursor", "goose", "cursor+goose", "ollama", or "anthropic"
                                              # cursor+goose: Cursor handles coding, Goose orchestrates
                                              #   validation (MCP tools: compile_quick, run_module_tests,
                                              #   find_similar_code, get_test_template, sync_resources)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"

    # ── GitLab ──
    gitlab_url: str = ""                    # e.g. https://gitlab.yourcompany.com
    gitlab_token: str = ""                  # Personal/Project access token
    gitlab_project_id: str = ""             # Numeric project ID or "group/project" path
    gitlab_default_branch: str = "main"     # Default branch name

    # ── Zoho Cliq ──
    cliq_webhook_token: str = ""
    cliq_bot_api_url: str = ""
    cliq_auth_token: str = ""                # Legacy: OAuth token (optional if zapikey is set)
    cliq_bot_zapikey: str = ""               # Bot ZAPI key — no OAuth needed
    cliq_bot_unique_name: str = ""           # Bot unique name (e.g. "saturnbot")
    cliq_channel_unique_name: str = ""       # Channel unique name for message API
    cliq_chat_id: str = ""                   # Chat ID for thread replies (CT_xxx)
    cliq_polling_mode: bool = True           # Poll Cliq for messages (no public URL needed)
    cliq_poll_interval: int = 5              # Polling interval in seconds

    # ── Server ──
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # ── Agent (per-repo instance) ──
    max_loop_iterations: int = 20

    # The single repo this Saturn instance watches
    repo_url: str = ""                      # e.g. https://gitlab.yourcompany.com/group/repo.git
    repo_local_path: str = "/data/saturn/repo"
    worktree_base_dir: str = "/data/saturn/tasks"

    # ── MCP Server (Toolshed — Layer 2) ──
    # Saturn MCP server is launched by Goose automatically when the
    # `saturn-zdpas` extension is registered in ~/.config/goose/config.yaml.
    # These settings control how the MCP server behaves.
    mcp_enabled: bool = True          # Register Saturn MCP server with Goose
    # DPAAS_HOME, BUILD_FILE_HOME, and GITLAB_RUNNER_DPAAS_HOME must be set
    # in the runner VM shell profile or passed as environment variables.
    # Hard-coded defaults are intentionally NOT provided — every deployment
    # has a different filesystem layout.  Set them explicitly:
    #   export DPAAS_HOME=/opt/dpaas
    #   export BUILD_FILE_HOME=/home/gitlab-runner/build-files
    # These three settings act as explicit overrides when the system env is absent.
    # Leave them empty ("") in saturn.env to rely solely on the system environment.
    saturn_dpaas_home: str = ""       # override for DPAAS_HOME (leave empty to use system env)
    saturn_build_file_home: str = ""  # override for BUILD_FILE_HOME (leave empty to use system env)
    gitlab_runner_dpaas_home: str = ""  # runner's own DPAAS_HOME (used by setup_saturn_dpaas.sh)

    # ── DPAAS Source Tars (provided per branch by CI/CD) ──
    # Saturn extracts these tars to bootstrap the compilation classpath.
    # Paths can be absolute or relative to the worktree root.
    # The setup gate uses these; set them in saturn.env or pass via env vars.
    dpaas_source_tar: str = "build/ZDPAS/output/dpaas.tar.gz"      # main source + jars tar
    dpaas_test_tar: str = "build/ZDPAS/output/dpaas_test.tar.gz"   # test source + resources tar

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

    @property
    def dpaas_home(self) -> Path | None:
        """
        Saturn's isolated DPAAS_HOME directory.

        Returns None when no DPAAS_HOME is configured (no hard-coded default).
        Callers should use the system environment variable DPAAS_HOME directly
        and only fall back to this property when an explicit override is needed.
        """
        if not self.saturn_dpaas_home:
            return None
        p = Path(self.saturn_dpaas_home)
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()

