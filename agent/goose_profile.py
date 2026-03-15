"""
Saturn Goose Profile Manager.

Creates and manages a `saturn-zdpas` Goose profile and registers the
Saturn MCP server (Toolshed) so Goose can call ZDPAS-specific tools:
  compile_quick, run_module_tests, search_code, get_module_context, etc.

The profile is written to the standard Goose config location:
  ~/.config/goose/profiles.yaml    — model + toolkit configuration
  ~/.config/goose/config.yaml      — MCP extension registration

Usage:
    from agent.goose_profile import ensure_saturn_profile
    ensure_saturn_profile(workspace="/path/to/zdpas")
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from config import settings


PROFILE_NAME = ""


def ensure_saturn_profile(workspace: str = ".") -> str:
    """
    Ensure the `saturn-zdpas` Goose profile and MCP extension are registered.

    1. Writes `~/.config/goose/profiles.yaml` with the Saturn profile
       (provider + model from saturn.env).
    2. Writes `~/.config/goose/config.yaml` registering the Saturn MCP
       server as a Goose extension.

    Returns the profile name to pass to `goose run --profile`.
    """
    config_dir = Path.home() / ".config" / "goose"
    config_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: profiles.yaml ──
    _write_profiles_yaml(config_dir)

    # ── Step 2: config.yaml (MCP extension) ──
    _write_mcp_extension(config_dir, workspace)

    return PROFILE_NAME


def _write_profiles_yaml(config_dir: Path):
    """Write the saturn-zdpas profile to profiles.yaml."""
    profiles_path = config_dir / "profiles.yaml"

    existing: dict = {}
    if profiles_path.exists():
        try:
            existing = yaml.safe_load(profiles_path.read_text()) or {}
        except Exception:
            existing = {}

    provider = settings.goose_provider or os.environ.get("GOOSE_PROVIDER", "anthropic")
    model = settings.goose_model or os.environ.get(
        "GOOSE_MODEL", "claude-3-5-sonnet-20241022"
    )

    saturn_profile = {
        "provider": provider,
        "processor": model,
        "accelerator": _accelerator_for(provider, model),
        "moderator": "truncate",
        "toolkits": [
            {"name": "developer", "requires": {}},
        ],
    }

    existing_profiles = existing.get("profiles", {})
    if existing_profiles.get(PROFILE_NAME, {}).get("provider") != provider or \
       existing_profiles.get(PROFILE_NAME, {}).get("processor") != model:
        existing_profiles[PROFILE_NAME] = saturn_profile
        existing["profiles"] = existing_profiles
        try:
            profiles_path.write_text(yaml.dump(existing, default_flow_style=False))
            print(f"  🪿  Goose profile '{PROFILE_NAME}' set "
                  f"(provider={provider}, model={model})")
        except PermissionError:
            print(f"  ⚠️  Could not write Goose profiles to {profiles_path}")


def _write_mcp_extension(config_dir: Path, workspace: str):
    """
    Register the Saturn MCP server as a Goose extension in config.yaml.

    Goose extension config:
        extensions:
          saturn-zdpas:
            type: stdio
            cmd: python
            args: ["-m", "mcp.server", "--workspace", "<workspace>"]
            env_keys: [DPAAS_HOME, DPAAS_SOURCE_TAR, DPAAS_TEST_TAR, BUILD_FILE_HOME]
            timeout: 120
            description: "Saturn ZDPAS tools — quick compile, tests, code search"
    """
    config_path = config_dir / "config.yaml"

    existing: dict = {}
    if config_path.exists():
        try:
            existing = yaml.safe_load(config_path.read_text()) or {}
        except Exception:
            existing = {}

    # Saturn MCP server invocation
    python_bin = sys.executable
    saturn_home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    mcp_entry = {
        "type": "stdio",
        "cmd": python_bin,
        "args": [
            "-m", "mcp.server",
            "--workspace", str(Path(workspace).resolve()),
        ],
        "env_keys": [
            "DPAAS_HOME",
            "DPAAS_SOURCE_TAR",
            "DPAAS_TEST_TAR",
            "BUILD_FILE_HOME",
        ],
        "timeout": 120,
        "description": (
            "Saturn ZDPAS Toolshed — quick compile (Tier 1), "
            "module tests (Tier 2), code search, project context"
        ),
        "enabled": True,
    }

    # Add PYTHONPATH so `import mcp.server` finds saturn's mcp package
    mcp_entry["env"] = {"PYTHONPATH": saturn_home}

    extensions = existing.setdefault("extensions", {})

    # Only update if workspace or key settings changed
    existing_ws = (
        extensions.get("saturn-zdpas", {})
        .get("args", ["", "", "", ""])[-1]
    )
    if existing_ws != str(Path(workspace).resolve()):
        extensions["saturn-zdpas"] = mcp_entry
        existing["extensions"] = extensions
        try:
            config_path.write_text(yaml.dump(existing, default_flow_style=False))
            print(f"  🔧  Saturn MCP registered in Goose extensions "
                  f"(workspace={Path(workspace).resolve()})")
        except PermissionError:
            print(f"  ⚠️  Could not write Goose config to {config_path}")
    else:
        print(f"  🔧  Saturn MCP extension already registered")


def _accelerator_for(provider: str, model: str) -> str:
    """Pick a fast 'accelerator' (haiku/mini) model for the same provider."""
    accelerators = {
        "anthropic": "claude-3-5-haiku-20241022",
        "openai": "gpt-4o-mini",
        "ollama": model,
    }
    return accelerators.get(provider, model)

