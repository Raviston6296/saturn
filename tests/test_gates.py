"""
Tests for the deterministic gates subsystem:
  - gates/config.py   — config loader & auto-discovery
  - gates/risk.py     — patch risk checker
  - gates/incremental.py — module mapping & targeted gates
  - gates/executor.py — sequential gate runner with retry
  - gates/__init__.py — GatePipeline orchestrator
"""

import subprocess
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from gates.config import (
    GateDef,
    GatesConfig,
    ModuleMapping,
    TestMapping,
    RulesConfig,
    RiskConfig,
    SaturnRepoConfig,
    load_repo_config,
    _load_gates,
    _load_rules,
    _load_risk,
    _detect_gates,
)
from gates.risk import RiskVerdict, check_risk
from gates.incremental import (
    get_affected_modules,
    get_test_patterns_for_modules,
    build_targeted_gates,
)
from gates.executor import (
    GateResult,
    PipelineResult,
    run_gate_pipeline,
)
from gates import GatePipeline, GatePipelineResult


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def saturn_config_dir(tmp_path):
    """Create a .saturn/ config dir with all three YAML files."""
    saturn = tmp_path / ".saturn"
    saturn.mkdir()

    (saturn / "gates.yaml").write_text("""
version: 1
gates:
  format:
    description: "Check formatting"
    command: "echo format-ok"
    retryable: true
  compile:
    description: "Compile project"
    command: "echo compile-ok"
    retryable: true
  fast-tests:
    description: "Run fast tests"
    command: "echo tests-ok"
    retryable: false
""")

    (saturn / "rules.yaml").write_text("""
version: 1
incremental:
  compile_strategy: "full"
  module_mapping:
    - path: "services/auth"
      module: "auth"
    - path: "services/billing"
      module: "billing"
  test_mapping:
    auth:
      pattern: "com.company.auth.*"
    billing:
      pattern: "com.company.billing.*"
""")

    (saturn / "risk.yaml").write_text("""
version: 1
risk_limits:
  max_files_changed: 10
  max_lines_changed: 500
restricted_paths:
  - infra/
  - terraform/
restricted_files:
  - .env
  - secrets.yml
""")

    return tmp_path


@pytest.fixture
def git_workspace(tmp_path):
    """Create a minimal git workspace for testing."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
    (tmp_path / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True)
    return tmp_path


# ──────────────────────────────────────────────────────────────────
# gates/config.py — GateDef & data models
# ──────────────────────────────────────────────────────────────────

class TestGateDef:
    def test_defaults(self):
        g = GateDef("compile")
        assert g.name == "compile"
        assert g.retryable is False
        assert g.command == ""

    def test_full_construction(self):
        g = GateDef("lint", description="Run linter", command="eslint .", retryable=True)
        assert g.retryable is True


class TestRulesConfig:
    def test_compile_strategy_default(self):
        rc = RulesConfig()
        assert rc.compile_strategy == "incremental"

    def test_compile_strategy_custom(self):
        rc = RulesConfig(compile_strategy="full")
        assert rc.compile_strategy == "full"


# ──────────────────────────────────────────────────────────────────
# gates/config.py — YAML loaders
# ──────────────────────────────────────────────────────────────────

class TestLoadGates:
    def test_dict_format(self, tmp_path):
        (tmp_path / "gates.yaml").write_text("""
version: 1
gates:
  compile:
    description: "Compile"
    command: "sbt compile"
    retryable: true
  test:
    description: "Test"
    command: "sbt test"
    retryable: false
""")
        cfg = _load_gates(tmp_path / "gates.yaml")
        assert cfg.version == 1
        assert len(cfg.gates) == 2
        names = [g.name for g in cfg.gates]
        assert "compile" in names
        assert "test" in names
        compile_gate = next(g for g in cfg.gates if g.name == "compile")
        assert compile_gate.retryable is True

    def test_list_format(self, tmp_path):
        (tmp_path / "gates.yaml").write_text("""
version: 1
gates:
  - name: format
    command: "sbt scalafmt"
    retryable: true
  - name: compile
    command: "sbt compile"
    retryable: false
""")
        cfg = _load_gates(tmp_path / "gates.yaml")
        assert len(cfg.gates) == 2
        assert cfg.gates[0].name == "format"

    def test_missing_file_returns_empty(self, tmp_path):
        cfg = _load_gates(tmp_path / "nonexistent.yaml")
        assert cfg.gates == []

    def test_invalid_yaml_returns_empty(self, tmp_path):
        (tmp_path / "gates.yaml").write_text("{{{{invalid}}}}")
        cfg = _load_gates(tmp_path / "gates.yaml")
        assert cfg.gates == []


class TestLoadRules:
    def test_parses_module_and_test_mappings(self, tmp_path):
        (tmp_path / "rules.yaml").write_text("""
version: 1
incremental:
  compile_strategy: "full"
  module_mapping:
    - path: "src/auth"
      module: "auth"
  test_mapping:
    auth:
      pattern: "com.example.auth.*"
""")
        cfg = _load_rules(tmp_path / "rules.yaml")
        assert cfg.compile_strategy == "full"
        assert len(cfg.module_mappings) == 1
        assert cfg.module_mappings[0].path == "src/auth"
        assert cfg.module_mappings[0].module == "auth"
        assert len(cfg.test_mappings) == 1
        assert cfg.test_mappings[0].pattern == "com.example.auth.*"

    def test_compile_strategy_defaults_to_incremental(self, tmp_path):
        (tmp_path / "rules.yaml").write_text("""
version: 1
incremental:
  module_mapping: []
""")
        cfg = _load_rules(tmp_path / "rules.yaml")
        assert cfg.compile_strategy == "incremental"

    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = _load_rules(tmp_path / "missing.yaml")
        assert cfg.module_mappings == []
        assert cfg.compile_strategy == "incremental"


class TestLoadRisk:
    def test_parses_risk_limits(self, tmp_path):
        (tmp_path / "risk.yaml").write_text("""
version: 1
risk_limits:
  max_files_changed: 25
  max_lines_changed: 1500
restricted_paths:
  - build/
  - .saturn/
restricted_files:
  - .env
""")
        cfg = _load_risk(tmp_path / "risk.yaml")
        assert cfg.max_files_changed == 25
        assert cfg.max_lines_changed == 1500
        assert "build/" in cfg.restricted_paths
        assert ".env" in cfg.restricted_files

    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = _load_risk(tmp_path / "missing.yaml")
        assert cfg.max_files_changed == 20
        assert cfg.max_lines_changed == 1000


class TestLoadRepoConfig:
    def test_with_saturn_dir(self, saturn_config_dir):
        cfg = load_repo_config(saturn_config_dir)
        assert cfg.has_config is True
        assert len(cfg.gates.gates) == 3
        assert cfg.rules.compile_strategy == "full"
        assert cfg.risk.max_files_changed == 10

    def test_without_saturn_dir_auto_discovers(self, tmp_path):
        # Python project — pyproject.toml triggers Python auto-discovery
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n")
        cfg = load_repo_config(tmp_path)
        assert cfg.has_config is False
        assert len(cfg.gates.gates) > 0

    def test_without_saturn_dir_sbt(self, tmp_path):
        (tmp_path / "build.sbt").write_text('name := "test"')
        cfg = load_repo_config(tmp_path)
        assert cfg.has_config is False
        gate_commands = [g.command for g in cfg.gates.gates]
        assert any("sbt" in cmd for cmd in gate_commands)

    def test_without_saturn_dir_maven(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project/>")
        cfg = load_repo_config(tmp_path)
        assert cfg.has_config is False
        gate_commands = [g.command for g in cfg.gates.gates]
        assert any("mvn" in cmd for cmd in gate_commands)

    def test_without_saturn_dir_go(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/test\ngo 1.21\n")
        cfg = load_repo_config(tmp_path)
        gate_commands = [g.command for g in cfg.gates.gates]
        assert any("go" in cmd for cmd in gate_commands)

    def test_without_saturn_dir_unknown(self, tmp_path):
        cfg = load_repo_config(tmp_path)
        assert cfg.has_config is False
        assert cfg.gates.gates == []


class TestDetectGates:
    def test_ant_in_root(self, tmp_path):
        (tmp_path / "build.xml").write_text("<project/>")
        gates, label = _detect_gates(tmp_path)
        assert "Ant" in label
        assert any("ant" in g.command for g in gates)

    def test_ant_in_build_subdir(self, tmp_path):
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "build.xml").write_text("<project/>")
        gates, label = _detect_gates(tmp_path)
        assert "Ant" in label
        assert any("-f build/build.xml" in g.command for g in gates)

    def test_nodejs_with_scripts(self, tmp_path):
        import json
        pkg = {"scripts": {"lint": "eslint .", "build": "tsc", "test": "jest"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        gates, label = _detect_gates(tmp_path)
        assert label == "Node.js"
        names = [g.name for g in gates]
        assert "lint" in names
        assert "compile" in names
        assert "fast-tests" in names

    def test_rust(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'test'\n")
        gates, label = _detect_gates(tmp_path)
        assert "Rust" in label

    def test_gradle(self, tmp_path):
        (tmp_path / "build.gradle").write_text("// gradle")
        gates, label = _detect_gates(tmp_path)
        assert "Gradle" in label
        assert any("gradlew" in g.command for g in gates)

    def test_unknown_project(self, tmp_path):
        gates, label = _detect_gates(tmp_path)
        assert gates == []
        assert label == "unknown"


# ──────────────────────────────────────────────────────────────────
# gates/risk.py
# ──────────────────────────────────────────────────────────────────

class TestRiskVerdict:
    def test_passed_default(self):
        v = RiskVerdict()
        assert v.passed is True
        assert v.violations == []

    def test_summary_passed(self):
        assert "passed" in RiskVerdict().summary.lower()

    def test_summary_failed(self):
        v = RiskVerdict(passed=False, violations=["too many files"])
        assert "FAILED" in v.summary
        assert "too many files" in v.summary


class TestCheckRisk:
    def test_no_changes_passes(self, tmp_path):
        config = RiskConfig()
        verdict = check_risk(tmp_path, config, changed_files=[])
        assert verdict.passed is True

    def test_too_many_files(self, tmp_path):
        config = RiskConfig(max_files_changed=2)
        verdict = check_risk(tmp_path, config, changed_files=["a.py", "b.py", "c.py"])
        assert verdict.passed is False
        assert any("Files changed: 3" in v for v in verdict.violations)

    def test_within_file_limit(self, tmp_path):
        config = RiskConfig(max_files_changed=5)
        verdict = check_risk(tmp_path, config, changed_files=["a.py", "b.py"])
        # Lines check may or may not pass depending on git; just check file limit passed
        file_violations = [v for v in verdict.violations if "Files changed" in v]
        assert file_violations == []

    def test_restricted_path_blocked(self, tmp_path):
        config = RiskConfig(restricted_paths=["infra/"])
        verdict = check_risk(tmp_path, config, changed_files=["infra/network.tf"])
        assert verdict.passed is False
        assert any("infra/" in v for v in verdict.violations)

    def test_restricted_path_not_triggered_for_unrelated(self, tmp_path):
        config = RiskConfig(
            restricted_paths=["infra/"],
            max_files_changed=100,
            max_lines_changed=100000,
        )
        verdict = check_risk(tmp_path, config, changed_files=["src/main.py"])
        path_violations = [v for v in verdict.violations if "Restricted path" in v]
        assert path_violations == []

    def test_restricted_file_blocked(self, tmp_path):
        config = RiskConfig(restricted_files=[".env"])
        verdict = check_risk(tmp_path, config, changed_files=[".env"])
        assert verdict.passed is False
        assert any(".env" in v for v in verdict.violations)

    def test_multiple_violations(self, tmp_path):
        config = RiskConfig(
            max_files_changed=1,
            restricted_paths=["infra/"],
        )
        verdict = check_risk(
            tmp_path, config,
            changed_files=["infra/main.tf", "src/app.py", "src/utils.py"],
        )
        assert verdict.passed is False
        assert len(verdict.violations) >= 2


# ──────────────────────────────────────────────────────────────────
# gates/incremental.py
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def rules_with_mappings():
    return RulesConfig(
        module_mappings=[
            ModuleMapping(path="services/auth", module="auth"),
            ModuleMapping(path="services/billing", module="billing"),
            ModuleMapping(path="services/common", module="common"),
        ],
        test_mappings=[
            TestMapping(module="auth", pattern="com.company.auth.*"),
            TestMapping(module="billing", pattern="com.company.billing.*"),
        ],
    )


class TestGetAffectedModules:
    def test_maps_single_file(self, rules_with_mappings):
        modules = get_affected_modules(
            ["services/auth/UserService.scala"],
            rules_with_mappings,
        )
        assert modules == {"auth"}

    def test_maps_multiple_modules(self, rules_with_mappings):
        modules = get_affected_modules(
            ["services/auth/Auth.scala", "services/billing/Invoice.scala"],
            rules_with_mappings,
        )
        assert modules == {"auth", "billing"}

    def test_unmapped_file_ignored(self, rules_with_mappings):
        modules = get_affected_modules(
            ["README.md", "build.sbt"],
            rules_with_mappings,
        )
        assert modules == set()

    def test_exact_path_match(self, rules_with_mappings):
        # Exact path match (not just prefix)
        modules = get_affected_modules(
            ["services/auth"],
            rules_with_mappings,
        )
        assert "auth" in modules

    def test_no_mappings_returns_empty(self):
        rules = RulesConfig()
        modules = get_affected_modules(["src/main.py"], rules)
        assert modules == set()

    def test_windows_style_paths(self, rules_with_mappings):
        modules = get_affected_modules(
            ["services\\auth\\UserService.scala"],
            rules_with_mappings,
        )
        assert "auth" in modules


class TestGetTestPatterns:
    def test_returns_patterns_for_modules(self, rules_with_mappings):
        patterns = get_test_patterns_for_modules({"auth"}, rules_with_mappings)
        assert "com.company.auth.*" in patterns

    def test_multiple_modules(self, rules_with_mappings):
        patterns = get_test_patterns_for_modules({"auth", "billing"}, rules_with_mappings)
        assert len(patterns) == 2

    def test_module_without_test_mapping(self, rules_with_mappings):
        # "common" has a module mapping but no test mapping
        patterns = get_test_patterns_for_modules({"common"}, rules_with_mappings)
        assert patterns == []


class TestBuildTargetedGates:
    def test_no_mappings_returns_original(self):
        rules = RulesConfig()
        gates = [GateDef("compile", command="sbt compile")]
        result = build_targeted_gates(gates, ["src/main.scala"], rules)
        assert result is gates  # same object returned

    def test_no_affected_modules_returns_original(self, rules_with_mappings):
        gates = [GateDef("compile", command="sbt compile")]
        result = build_targeted_gates(gates, ["README.md"], rules_with_mappings)
        assert result is gates

    def test_substitutes_modules_token(self, rules_with_mappings):
        gates = [GateDef("compile", command="sbt 'project {modules}' compile")]
        result = build_targeted_gates(
            gates, ["services/auth/Auth.scala"], rules_with_mappings
        )
        assert "{modules}" not in result[0].command
        assert "auth" in result[0].command

    def test_substitutes_test_patterns_token(self, rules_with_mappings):
        gates = [GateDef("test", command="sbt 'testOnly {test_patterns}'")]
        result = build_targeted_gates(
            gates, ["services/auth/Auth.scala"], rules_with_mappings
        )
        assert "{test_patterns}" not in result[0].command
        assert "com.company.auth" in result[0].command

    def test_substitutes_test_w_flags_token(self, rules_with_mappings):
        gates = [GateDef("test", command="scalatest {test_w_flags}")]
        result = build_targeted_gates(
            gates, ["services/auth/Auth.scala"], rules_with_mappings
        )
        assert "{test_w_flags}" not in result[0].command
        # Pattern "com.company.auth.*" → strip .* → "-w com.company.auth"
        assert "-w com.company.auth" in result[0].command

    def test_test_w_flags_strips_wildcard(self, rules_with_mappings):
        """Ensure .* is stripped when building -w flags."""
        gates = [GateDef("test", command="{test_w_flags}")]
        result = build_targeted_gates(
            gates, ["services/billing/Invoice.scala"], rules_with_mappings
        )
        cmd = result[0].command
        # Should be "-w com.company.billing" not "-w com.company.billing.*"
        assert cmd == "-w com.company.billing"

    def test_test_w_flags_uses_removesuffix_not_rstrip(self):
        """removesuffix('.*') must only strip the literal suffix '.*',
        not any combination of '.' and '*' characters.

        e.g. 'com.foo.bar.*' → '-w com.foo.bar'  (correct with removesuffix)
             'com.foo.bar.*' → '-w com.foo.b'     (wrong with rstrip('.*'))
        """
        from gates.config import TestMapping as TM
        rules = RulesConfig(
            module_mappings=[ModuleMapping(path="src", module="m")],
            test_mappings=[TM(module="m", pattern="com.foo.bar.*")],
        )
        gates = [GateDef("test", command="{test_w_flags}")]
        result = build_targeted_gates(gates, ["src/main.scala"], rules)
        # removesuffix strips exactly '.*' → com.foo.bar
        assert result[0].command == "-w com.foo.bar"
        # rstrip('.*') would strip all trailing '.' and '*' chars → 'com.foo.b' (WRONG)
        assert result[0].command != "-w com.foo.b"

    def test_preserves_gate_retryable(self, rules_with_mappings):
        gates = [GateDef("test", command="sbt test", retryable=True)]
        result = build_targeted_gates(
            gates, ["services/auth/Auth.scala"], rules_with_mappings
        )
        assert result[0].retryable is True

    def test_multiple_modules_in_substitution(self, rules_with_mappings):
        gates = [GateDef("test", command="{test_w_flags}")]
        result = build_targeted_gates(
            gates,
            ["services/auth/Auth.scala", "services/billing/Invoice.scala"],
            rules_with_mappings,
        )
        cmd = result[0].command
        assert "-w com.company.auth" in cmd
        assert "-w com.company.billing" in cmd


# ──────────────────────────────────────────────────────────────────
# gates/executor.py
# ──────────────────────────────────────────────────────────────────

class TestGateResult:
    def test_default_passed(self):
        r = GateResult(gate_name="compile", passed=True)
        assert r.passed is True
        assert r.attempts == 1

    def test_failed(self):
        r = GateResult(gate_name="test", passed=False, exit_code=1, output="FAIL")
        assert r.passed is False


class TestPipelineResult:
    def test_summary_with_results(self):
        result = PipelineResult(
            passed=True,
            gate_results=[
                GateResult("compile", passed=True),
                GateResult("test", passed=False),
            ],
        )
        summary = result.summary
        assert "compile" in summary
        assert "test" in summary

    def test_summary_shows_stopped_at(self):
        result = PipelineResult(passed=False, stopped_at="compile")
        assert "compile" in result.summary


class TestRunGatePipeline:
    def test_all_gates_pass(self, tmp_path):
        gates = [
            GateDef("step1", command="echo step1", retryable=False),
            GateDef("step2", command="echo step2", retryable=False),
        ]
        result = run_gate_pipeline(gates, tmp_path)
        assert result.passed is True
        assert len(result.gate_results) == 2
        assert all(gr.passed for gr in result.gate_results)

    def test_gate_fails_stops_pipeline(self, tmp_path):
        gates = [
            GateDef("pass", command="echo ok", retryable=False),
            GateDef("fail", command="exit 1", retryable=False),
            GateDef("never_reached", command="echo never", retryable=False),
        ]
        result = run_gate_pipeline(gates, tmp_path)
        assert result.passed is False
        assert result.stopped_at == "fail"
        # The third gate should never have run
        assert len(result.gate_results) == 2

    def test_retryable_gate_fixed_by_callback(self, tmp_path):
        """A retryable gate that fails, then the callback fixes it so it passes."""
        call_count = {"n": 0}

        # First call returns failing command, callback switches to passing command
        failing_cmd = "exit 1"
        passing_cmd = "echo ok"

        # We'll use a file to track state: if file exists, run passing command
        flag_file = tmp_path / "fixed"

        gates = [
            GateDef("compile", command=f"test -f {flag_file} && echo ok || exit 1", retryable=True),
        ]

        def fix_callback(gate_name, error_output, workspace):
            call_count["n"] += 1
            flag_file.touch()  # "fix" the problem
            return True

        result = run_gate_pipeline(gates, tmp_path, fix_callback=fix_callback, max_retries=3)
        assert result.passed is True
        assert call_count["n"] == 1  # callback was called once

    def test_retryable_gate_exhausts_retries(self, tmp_path):
        """A gate that always fails — exhausts retries and stops."""
        callback_calls = {"n": 0}

        gates = [GateDef("fail-always", command="exit 1", retryable=True)]

        def fix_callback(gate_name, error_output, workspace):
            callback_calls["n"] += 1
            return True  # claims to fix but doesn't

        result = run_gate_pipeline(gates, tmp_path, fix_callback=fix_callback, max_retries=2)
        assert result.passed is False
        assert result.total_retries == 2

    def test_non_retryable_gate_no_callback(self, tmp_path):
        """Non-retryable failing gate — callback should NOT be called."""
        callback_calls = {"n": 0}

        gates = [GateDef("fail", command="exit 1", retryable=False)]

        def fix_callback(gate_name, error_output, workspace):
            callback_calls["n"] += 1
            return True

        result = run_gate_pipeline(gates, tmp_path, fix_callback=fix_callback)
        assert result.passed is False
        assert callback_calls["n"] == 0  # callback was never called

    def test_gate_timeout_fails_gracefully(self, tmp_path):
        gates = [GateDef("slow", command="sleep 100", retryable=False)]
        result = run_gate_pipeline(gates, tmp_path, timeout_per_gate=1)
        assert result.passed is False
        assert "timed out" in result.gate_results[0].output

    def test_gate_with_empty_command_skipped(self, tmp_path):
        gates = [
            GateDef("no-cmd", command=""),
            GateDef("run", command="echo ok"),
        ]
        result = run_gate_pipeline(gates, tmp_path)
        assert result.passed is True
        # Only the gate with a command should appear in results
        assert len(result.gate_results) == 1
        assert result.gate_results[0].gate_name == "run"

    def test_captures_stdout_stderr(self, tmp_path):
        gates = [GateDef("output", command="echo hello_stdout; echo hello_stderr >&2", retryable=False)]
        result = run_gate_pipeline(gates, tmp_path)
        output = result.gate_results[0].output
        assert "hello_stdout" in output


# ──────────────────────────────────────────────────────────────────
# gates/__init__.py — GatePipeline orchestrator
# ──────────────────────────────────────────────────────────────────

class TestGatePipelineResult:
    def test_passed_when_both_pass(self):
        from gates.risk import RiskVerdict
        from gates.executor import PipelineResult
        result = GatePipelineResult(
            risk=RiskVerdict(passed=True),
            gates=PipelineResult(passed=True),
        )
        assert result.passed is True

    def test_failed_when_risk_fails(self):
        from gates.risk import RiskVerdict
        from gates.executor import PipelineResult
        result = GatePipelineResult(
            risk=RiskVerdict(passed=False),
            gates=PipelineResult(passed=True),
        )
        assert result.passed is False

    def test_skipped_always_passes(self):
        result = GatePipelineResult(skipped=True, skip_reason="No files changed")
        assert result.passed is True
        assert "skipped" in result.summary.lower()

    def test_summary_shows_changed_files_count(self):
        result = GatePipelineResult(changed_files=["a.py", "b.py"])
        assert "2" in result.summary


class TestGatePipeline:
    def test_no_files_changed_skips(self, saturn_config_dir):
        """Pipeline skips if there are no git changes."""
        pipeline = GatePipeline(workspace=saturn_config_dir)

        # Patch get_changed_files_vs_base to return empty list
        with patch("gates.incremental.get_changed_files_vs_base", return_value=[]):
            result = pipeline.run()

        assert result.skipped is True
        assert result.passed is True

    def test_unknown_project_no_gates_skips(self, tmp_path):
        """Pipeline skips when no gates are configured."""
        # tmp_path has no .saturn/ and no recognizable build files
        pipeline = GatePipeline(workspace=tmp_path)

        with patch("gates.incremental.get_changed_files_vs_base", return_value=["file.txt"]):
            result = pipeline.run()

        assert result.skipped is True
        assert "No gates" in result.skip_reason

    def test_risk_check_blocks_pipeline(self, saturn_config_dir):
        """Pipeline stops early when risk check fails."""
        # saturn_config_dir has max_files_changed=10
        pipeline = GatePipeline(workspace=saturn_config_dir)

        # Return more files than the limit
        many_files = [f"src/file{i}.py" for i in range(15)]
        # Patch in the gates package namespace (where __init__.py imported the function)
        with patch("gates.get_changed_files_vs_base", return_value=many_files), \
             patch("gates.risk._count_lines_changed", return_value=0):
            result = pipeline.run()

        assert result.passed is False
        assert not result.risk.passed

    def test_gates_run_and_pass(self, saturn_config_dir, git_workspace):
        """Full pipeline: gates defined in .saturn/ are executed and pass."""
        # saturn_config_dir and git_workspace may share tmp_path; .saturn/ may already exist.
        # Ensure .saturn/ is present in git_workspace (copy only if needed).
        import shutil
        if not (git_workspace / ".saturn").exists():
            shutil.copytree(saturn_config_dir / ".saturn", git_workspace / ".saturn")

        # Add a file so there are changes
        (git_workspace / "src.py").write_text("x = 1")
        subprocess.run(["git", "add", "."], cwd=git_workspace, capture_output=True)

        pipeline = GatePipeline(workspace=git_workspace)
        with patch("gates.get_changed_files_vs_base", return_value=["src.py"]), \
             patch("gates.risk._count_lines_changed", return_value=1):
            result = pipeline.run()

        assert result.passed is True
        assert result.gates.passed is True
        # All 3 gates should have run
        assert len(result.gates.gate_results) == 3

    def test_uses_auto_discovery_when_no_saturn_dir(self, git_workspace):
        """Pipeline falls back to auto-discovery for Python projects."""
        (git_workspace / "pyproject.toml").write_text("[project]\nname='test'\n")

        pipeline = GatePipeline(workspace=git_workspace)

        # Patch in the gates package namespace (where __init__.py imported the function)
        with patch("gates.get_changed_files_vs_base", return_value=["app.py"]), \
             patch("gates.risk._count_lines_changed", return_value=5), \
             patch("gates.run_gate_pipeline") as mock_run:
            mock_run.return_value = PipelineResult(passed=True)
            result = pipeline.run()

        assert result.has_config is False
        mock_run.assert_called_once()
