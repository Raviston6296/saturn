"""
Gate configuration loader — reads .saturn/{gates,rules,risk}.yaml from the repo.

If .saturn/ doesn't exist in the worktree, Saturn auto-discovers the
project type and generates default gates so validation always runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ── Data models ──────────────────────────────────────────────────

@dataclass
class GateDef:
    """A single deterministic gate (compile, lint, test, …)."""
    name: str
    description: str = ""
    command: str = ""
    retryable: bool = False


@dataclass
class ModuleMapping:
    """Maps a file-path prefix to a logical module name."""
    path: str
    module: str


@dataclass
class TestMapping:
    """Maps a module name to a test pattern/command."""
    module: str
    pattern: str


@dataclass
class GatesConfig:
    """Parsed .saturn/gates.yaml"""
    version: int = 1
    gates: list[GateDef] = field(default_factory=list)


@dataclass
class RulesConfig:
    """Parsed .saturn/rules.yaml (incremental validation)."""
    version: int = 1
    module_mappings: list[ModuleMapping] = field(default_factory=list)
    test_mappings: list[TestMapping] = field(default_factory=list)


@dataclass
class RiskConfig:
    """Parsed .saturn/risk.yaml (patch safety limits)."""
    version: int = 1
    max_files_changed: int = 20
    max_lines_changed: int = 1000
    restricted_paths: list[str] = field(default_factory=list)
    restricted_files: list[str] = field(default_factory=list)


@dataclass
class SaturnRepoConfig:
    """Complete repo-level Saturn configuration."""
    gates: GatesConfig = field(default_factory=GatesConfig)
    rules: RulesConfig = field(default_factory=RulesConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    has_config: bool = False


# ── Loader ───────────────────────────────────────────────────────

def load_repo_config(workspace: str | Path) -> SaturnRepoConfig:
    """
    Load .saturn/ config from a workspace (worktree or repo root).
    If .saturn/ doesn't exist, auto-discovers the project type and
    generates default gates so validation always runs.
    """
    workspace = Path(workspace)
    saturn_dir = workspace / ".saturn"

    if saturn_dir.is_dir():
        return SaturnRepoConfig(
            gates=_load_gates(saturn_dir / "gates.yaml"),
            rules=_load_rules(saturn_dir / "rules.yaml"),
            risk=_load_risk(saturn_dir / "risk.yaml"),
            has_config=True,
        )

    # No .saturn/ — auto-discover project type and build default config
    print("  ℹ️  No .saturn/ config — auto-discovering project type...")
    return _auto_discover_config(workspace)


# ── Auto-discovery ───────────────────────────────────────────────

_PROJECT_DETECTORS: list[tuple[str, str, list[GateDef], RiskConfig]] = []


def _auto_discover_config(workspace: Path) -> SaturnRepoConfig:
    """
    Detect build system from workspace files and return a default config
    with appropriate compile/test gates.
    """
    gates, project_type = _detect_gates(workspace)
    risk = _default_risk()

    if gates:
        print(f"  🔍 Detected project type: {project_type}")
        for g in gates:
            print(f"     • {g.name}: {g.command}")
    else:
        print("  ⚠️  Could not detect project type — no default gates")

    return SaturnRepoConfig(
        gates=GatesConfig(gates=gates),
        rules=RulesConfig(),
        risk=risk,
        has_config=False,
    )


def _detect_gates(workspace: Path) -> tuple[list[GateDef], str]:
    """Walk the workspace looking for known build files, return gates + type label."""

    # sbt (Scala)
    if (workspace / "build.sbt").exists():
        return [
            GateDef("compile", "Compile Scala project", "sbt compile", retryable=True),
            GateDef("fast-tests", "Run unit tests", "sbt test", retryable=True),
        ], "Scala (sbt)"

    # Ant — check workspace root and build/ subdirectory
    for build_xml in [workspace / "build.xml", workspace / "build" / "build.xml"]:
        if build_xml.exists():
            ant_dir = str(build_xml.parent.relative_to(workspace))
            prefix = f"-f {ant_dir}/build.xml " if ant_dir != "." else ""
            return [
                GateDef("compile", "Compile with Ant", f"ant {prefix}compile", retryable=True),
                GateDef("fast-tests", "Run Ant tests", f"ant {prefix}test", retryable=True),
            ], f"Java/Scala (Ant — {build_xml.relative_to(workspace)})"

    # Maven
    if (workspace / "pom.xml").exists():
        return [
            GateDef("compile", "Compile with Maven", "mvn compile -q", retryable=True),
            GateDef("fast-tests", "Run Maven tests", "mvn test -q", retryable=True),
        ], "Java (Maven)"

    # Gradle
    for gf in ["build.gradle", "build.gradle.kts"]:
        if (workspace / gf).exists():
            return [
                GateDef("compile", "Compile with Gradle", "./gradlew build -x test", retryable=True),
                GateDef("fast-tests", "Run Gradle tests", "./gradlew test", retryable=True),
            ], "Java/Kotlin (Gradle)"

    # Node.js
    if (workspace / "package.json").exists():
        gates = []
        import json
        try:
            pkg = json.loads((workspace / "package.json").read_text())
            scripts = pkg.get("scripts", {})
            if "lint" in scripts:
                gates.append(GateDef("lint", "Run linter", "npm run lint", retryable=True))
            if "build" in scripts:
                gates.append(GateDef("compile", "Build project", "npm run build", retryable=True))
            if "test" in scripts:
                gates.append(GateDef("fast-tests", "Run tests", "npm test", retryable=True))
        except Exception:
            gates = [GateDef("fast-tests", "Run tests", "npm test", retryable=True)]
        return gates, "Node.js"

    # Python (pyproject.toml, setup.py, or requirements.txt)
    if (workspace / "pyproject.toml").exists() or \
       (workspace / "setup.py").exists() or \
       (workspace / "requirements.txt").exists():
        return [
            GateDef("fast-tests", "Run pytest", "python -m pytest -q --tb=short", retryable=True),
        ], "Python"

    # Go
    if (workspace / "go.mod").exists():
        return [
            GateDef("compile", "Build Go project", "go build ./...", retryable=True),
            GateDef("fast-tests", "Run Go tests", "go test ./...", retryable=True),
        ], "Go"

    # Rust
    if (workspace / "Cargo.toml").exists():
        return [
            GateDef("compile", "Build Rust project", "cargo build", retryable=True),
            GateDef("fast-tests", "Run Rust tests", "cargo test", retryable=True),
        ], "Rust"

    return [], "unknown"


def _default_risk() -> RiskConfig:
    """Sensible default risk limits when no risk.yaml exists."""
    return RiskConfig(
        max_files_changed=20,
        max_lines_changed=1000,
        restricted_paths=["infra/", "terraform/", "database/migrations/"],
        restricted_files=[".env", "secrets.yml", "credentials.json"],
    )


def _load_gates(path: Path) -> GatesConfig:
    data = _read_yaml(path)
    if not data:
        return GatesConfig()

    gates_raw = data.get("gates", {})
    gates = []

    if isinstance(gates_raw, dict):
        # Dict format: gates: { format: {command: …}, lint: {command: …} }
        for name, props in gates_raw.items():
            if not isinstance(props, dict):
                continue
            gates.append(GateDef(
                name=name,
                description=props.get("description", ""),
                command=props.get("command", ""),
                retryable=props.get("retryable", False),
            ))
    elif isinstance(gates_raw, list):
        # List format: gates: [ {name: format, command: …}, … ]
        for item in gates_raw:
            if not isinstance(item, dict) or "name" not in item:
                continue
            gates.append(GateDef(
                name=item["name"],
                description=item.get("description", ""),
                command=item.get("command", ""),
                retryable=item.get("retryable", False),
            ))

    return GatesConfig(version=data.get("version", 1), gates=gates)


def _load_rules(path: Path) -> RulesConfig:
    data = _read_yaml(path)
    if not data:
        return RulesConfig()

    inc = data.get("incremental", {})

    module_mappings = [
        ModuleMapping(path=m["path"], module=m["module"])
        for m in inc.get("module_mapping", [])
        if isinstance(m, dict) and "path" in m and "module" in m
    ]

    test_mappings = [
        TestMapping(module=mod, pattern=props.get("pattern", ""))
        for mod, props in inc.get("test_mapping", {}).items()
        if isinstance(props, dict)
    ]

    return RulesConfig(
        version=data.get("version", 1),
        module_mappings=module_mappings,
        test_mappings=test_mappings,
    )


def _load_risk(path: Path) -> RiskConfig:
    data = _read_yaml(path)
    if not data:
        return RiskConfig()

    limits = data.get("risk_limits", {})

    return RiskConfig(
        version=data.get("version", 1),
        max_files_changed=limits.get("max_files_changed", 20),
        max_lines_changed=limits.get("max_lines_changed", 1000),
        restricted_paths=data.get("restricted_paths", []),
        restricted_files=data.get("restricted_files", []),
    )


def _read_yaml(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"  ⚠️  Failed to parse {path.name}: {e}")
        return None
