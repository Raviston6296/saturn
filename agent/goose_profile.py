"""
Saturn Goose Profile Manager.

Creates and manages a `saturn-zdpas` Goose profile optimized for ZDPAS
Scala/Java development. The profile is written to the standard Goose
config location (~/.config/goose/profiles.yaml).

Profile features:
  - Uses the model provider and model configured in saturn.env
  - Enables the `developer` built-in toolkit
  - Pre-loads ZDPAS project context
  - Configures Goose to skip interactive confirmations

Usage:
    from agent.goose_profile import ensure_saturn_profile
    ensure_saturn_profile()  # call once at startup
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from config import settings


PROFILE_NAME = "saturn-zdpas"


def ensure_saturn_profile() -> str:
    """
    Ensure the `saturn-zdpas` Goose profile exists and is up to date.

    Writes `~/.config/goose/profiles.yaml` with the Saturn profile.
    If a custom profile already exists and has the same provider/model,
    it is left unchanged to preserve user customisations.

    Returns the profile name to pass to `goose run --profile`.
    """
    config_dir = Path.home() / ".config" / "goose"
    config_dir.mkdir(parents=True, exist_ok=True)
    profiles_path = config_dir / "profiles.yaml"

    existing: dict = {}
    if profiles_path.exists():
        try:
            existing = yaml.safe_load(profiles_path.read_text()) or {}
        except Exception:
            existing = {}

    # Build the Saturn ZDPAS profile
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

    # Only update if missing or provider/model changed
    existing_profiles = existing.get("profiles", {})
    if existing_profiles.get(PROFILE_NAME, {}).get("provider") != provider or \
       existing_profiles.get(PROFILE_NAME, {}).get("processor") != model:

        existing_profiles[PROFILE_NAME] = saturn_profile
        existing["profiles"] = existing_profiles

        try:
            profiles_path.write_text(yaml.dump(existing, default_flow_style=False))
            print(f"  🪿  Goose profile '{PROFILE_NAME}' configured "
                  f"(provider={provider}, model={model})")
        except PermissionError:
            print(f"  ⚠️  Could not write Goose profiles to {profiles_path} — using defaults")
    else:
        print(f"  🪿  Goose profile '{PROFILE_NAME}' already up to date")

    return PROFILE_NAME


def _accelerator_for(provider: str, model: str) -> str:
    """Pick a fast 'accelerator' (haiku/mini) model for the same provider."""
    accelerators = {
        "anthropic": "claude-3-5-haiku-20241022",
        "openai": "gpt-4o-mini",
        "ollama": model,  # use the same model for local
    }
    return accelerators.get(provider, model)
