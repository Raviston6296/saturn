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


# ── ZDPAS-dedicated config builder ──────────────────────────────

def _auto_discover_config(workspace: Path) -> SaturnRepoConfig:
    """
    Saturn is dedicated to the ZDPAS Scala/Java project.

    When .saturn/gates.yaml is missing from the worktree, this function
    generates the standard ZDPAS 4-stage pipeline automatically.

    No generic project-type detection (Python/Go/Node/etc.) is attempted —
    this instance is purpose-built for ZDPAS.
    """
    is_zdpas = (
        (workspace / "build" / "ant.properties").exists()
        or (workspace / "source" / "com" / "zoho" / "dpaas").exists()
        or (
            (workspace / "build.xml").exists()
            and (workspace / "source").exists()
            and (workspace / "test").exists()
        )
    )

    if is_zdpas:
        print("  🔍 ZDPAS project detected — using 4-stage compilation pipeline")
        gates = _get_zdpas_gates(workspace)
    else:
        print(
            "  ⚠️  Not a recognised ZDPAS workspace (source/com/zoho/dpaas not found). "
            "Add .saturn/gates.yaml to configure gates explicitly."
        )
        gates = []

    return SaturnRepoConfig(
        gates=GatesConfig(gates=gates),
        rules=RulesConfig(),
        risk=_default_risk(),
        has_config=False,
    )


def _get_zdpas_gates(workspace: Path) -> list[GateDef]:
    """
    Return the explicit 4-stage ZDPAS gate pipeline.

    Stage 1 — setup   (not retryable)
        Extract dpaas.tar.gz (and optionally dpaas_test.tar.gz) provided
        for this branch to populate DPAAS_HOME with the baseline jars,
        libs, resources, and config files needed for compilation.

    Stage 2 — compile   (retryable)
        Joint-compile all Java and Scala sources in ./source/ using scalac
        then javac, matching the CI/CD build_dpaas_jar_from_cache stage.
        Produces a new dpaas.jar placed at the runtime path.

    Stage 3 — build-test-jar   (retryable)
        Compile test sources in ./test/source/ against the new dpaas.jar.
        Produces dpaas_test.jar in the worktree root.

    Stage 4 — unit-tests   (retryable)
        Run ScalaTest for the modules affected by the agent's changes.
        The set of modules is passed via SATURN_TEST_MODULES (set by the
        incremental gate runner based on changed files).
        Falls back to running all tests when SATURN_TEST_MODULES is empty.

    Environment variables consumed by the gates:
        DPAAS_HOME          — runtime root (e.g. /opt/dpaas on runner VM)
        DPAAS_SOURCE_TAR    — override path to dpaas.tar.gz
        DPAAS_TEST_TAR      — override path to dpaas_test.tar.gz
        BUILD_FILE_HOME     — directory containing datastore.json
        SATURN_TEST_MODULES — comma-separated modules/suites to test
    """
    # ─── Gate 1: setup ───────────────────────────────────────────────────────
    setup_cmd = r'''
set -e
echo "━━━ Gate 1/4: Setup ━━━"
echo "  DPAAS_HOME: $DPAAS_HOME"

# ── Validate DPAAS_HOME ──
if [[ -z "$DPAAS_HOME" ]]; then
    echo "❌ DPAAS_HOME is not set."
    echo "   Export it in saturn.env or in the runner VM shell profile:"
    echo "   export DPAAS_HOME=/opt/dpaas"
    exit 1
fi

# ── Locate source tar ──
SOURCE_TAR="${DPAAS_SOURCE_TAR:-}"
if [[ -z "$SOURCE_TAR" ]]; then
    # Default: CI/CD cache location inside the worktree
    if [[ -f "build/ZDPAS/output/dpaas.tar.gz" ]]; then
        SOURCE_TAR="build/ZDPAS/output/dpaas.tar.gz"
    fi
fi

if [[ -z "$SOURCE_TAR" ]] || [[ ! -f "$SOURCE_TAR" ]]; then
    echo "❌ dpaas.tar.gz not found."
    echo "   Expected at: build/ZDPAS/output/dpaas.tar.gz"
    echo "   Or set: export DPAAS_SOURCE_TAR=/path/to/dpaas.tar.gz"
    exit 1
fi

echo "  📦 Source tar: $SOURCE_TAR"

# ── Extract source tar → DPAAS_HOME ──
if [[ -d "$DPAAS_HOME/zdpas" ]]; then
    rm -rf "$DPAAS_HOME/zdpas"
fi
tar -xf "$SOURCE_TAR" -C "$DPAAS_HOME"
mkdir -p "$DPAAS_HOME/zdpas/spark/app_blue"

# ── Verify baseline jars were extracted ──
JAR_COUNT=$(ls "$DPAAS_HOME/zdpas/spark/jars/"*.jar 2>/dev/null | wc -l | tr -d ' ')
if [[ "$JAR_COUNT" -eq 0 ]]; then
    echo "❌ No jars found in $DPAAS_HOME/zdpas/spark/jars/ after extraction"
    exit 1
fi
echo "  ✅ Extracted $JAR_COUNT jars to $DPAAS_HOME/zdpas/spark/jars/"

# ── Optionally extract test tar (adds test resources) ──
TEST_TAR="${DPAAS_TEST_TAR:-}"
if [[ -z "$TEST_TAR" ]]; then
    if [[ -f "build/ZDPAS/output/dpaas_test.tar.gz" ]]; then
        TEST_TAR="build/ZDPAS/output/dpaas_test.tar.gz"
    fi
fi

if [[ -n "$TEST_TAR" ]] && [[ -f "$TEST_TAR" ]]; then
    echo "  📦 Test tar: $TEST_TAR"
    mkdir -p /tmp/dpaas_test_tar
    tar -xf "$TEST_TAR" -C /tmp/dpaas_test_tar 2>/dev/null || true
    cp -rn /tmp/dpaas_test_tar/zdpas/spark/resources/* \
        "$DPAAS_HOME/zdpas/spark/resources/" 2>/dev/null || true
    cp -r /tmp/dpaas_test_tar/zdpas/spark/jars/* \
        "$DPAAS_HOME/zdpas/spark/jars/" 2>/dev/null || true
    rm -rf /tmp/dpaas_test_tar
    echo "  ✅ Test tar merged"
fi

# ── datastore.json ──
if [[ -n "$BUILD_FILE_HOME" ]] && [[ -f "$BUILD_FILE_HOME/datastore.json" ]]; then
    cp "$BUILD_FILE_HOME/datastore.json" \
       "$DPAAS_HOME/zdpas/spark/resources/datastore.json"
    echo "  ✅ Copied datastore.json from BUILD_FILE_HOME"
fi

# ── log4j config ──
mkdir -p "$DPAAS_HOME/zdpas/spark/conf"
LOG4J="$DPAAS_HOME/zdpas/spark/conf/log4j-local.properties"
if [[ ! -f "$LOG4J" ]]; then
    if [[ -f "./resources/log4j.properties" ]]; then
        cp ./resources/log4j.properties "$LOG4J"
    else
        cat > "$LOG4J" <<'LOG4JEOF'
log4j.rootLogger=WARN, console
log4j.appender.console=org.apache.log4j.ConsoleAppender
log4j.appender.console.layout=org.apache.log4j.PatternLayout
log4j.appender.console.layout.ConversionPattern=%d{HH:mm:ss} %-5p %c{1} - %m%n
LOG4JEOF
    fi
fi

echo "✅ Setup complete — DPAAS_HOME ready at $DPAAS_HOME"
'''

    # ─── Gate 2: compile ─────────────────────────────────────────────────────
    compile_cmd = r'''
set -e
echo "━━━ Gate 2/4: Compile ━━━"

# Find sources — matching CI/CD build_dpaas_jar_from_cache exactly
find ./source -name "*.java" -type f \
    | grep -v -E '^./source/(Main|Test)\.' > all_java.txt 2>/dev/null || touch all_java.txt
find ./source -name "*.scala" -type f \
    | grep -v -E "^./source/(Main|Test|Generate)" > scala_files.txt
cat all_java.txt scala_files.txt > all_sources.txt

JAVA_COUNT=$(wc -l < all_java.txt | tr -d ' ')
SCALA_COUNT=$(wc -l < scala_files.txt | tr -d ' ')
echo "  Found $JAVA_COUNT Java and $SCALA_COUNT Scala files"

mkdir -p compiled_classes

# Step 1: Joint-compile Java+Scala with scalac
echo "  Step 1: scalac joint compilation..."
scalac \
    -cp "$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/lib/*" \
    -J-Xmx2g \
    -d compiled_classes \
    @all_sources.txt

# Step 2: Compile Java with javac (produces proper .class files)
if [[ "$JAVA_COUNT" -gt 0 ]]; then
    echo "  Step 2: javac compilation..."
    javac \
        -cp "compiled_classes:$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/lib/*" \
        -sourcepath ./source \
        -d compiled_classes \
        @all_java.txt
fi

# Step 3: Create dpaas.jar and set it at the runtime path
echo "  Step 3: Creating dpaas.jar..."
mkdir -p "$DPAAS_HOME/zdpas/spark/app_blue"
jar cf "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar" -C compiled_classes .

if [[ -d "./resources" ]]; then
    jar uf "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar" \
        -C ./resources . 2>/dev/null || true
fi

# Keep a local copy for test compilation
cp "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar" ./dpaas.jar

rm -rf compiled_classes all_java.txt scala_files.txt all_sources.txt

ls -lh "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar"
echo "✅ Compile done — dpaas.jar at runtime path"
'''

    # ─── Gate 3: build-test-jar ──────────────────────────────────────────────
    build_test_cmd = r'''
set -e
echo "━━━ Gate 3/4: Build test JAR ━━━"

test -f dpaas.jar || { echo "❌ dpaas.jar not found (compile gate must run first)"; exit 1; }

find ./test/source -name "*.scala" -type f > source_test.txt
TEST_COUNT=$(wc -l < source_test.txt | tr -d ' ')
echo "  Found $TEST_COUNT test Scala files"

mkdir -p test_compiled_classes

scalac \
    -cp "dpaas.jar:$DPAAS_HOME/zdpas/spark/app_blue/ExpParser.jar:$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/lib/*" \
    -J-Xmx2g \
    -d test_compiled_classes \
    @source_test.txt

jar cf dpaas_test.jar -C test_compiled_classes .

if [[ -d "./test/resources" ]]; then
    jar uf dpaas_test.jar -C ./test/resources . 2>/dev/null || true
fi

rm -rf test_compiled_classes source_test.txt

ls -lh dpaas_test.jar
echo "✅ Test JAR built"
'''

    # ─── Gate 4: unit-tests ──────────────────────────────────────────────────
    #
    # SATURN_TEST_MODULES is set by the incremental gate runner to the
    # comma-separated list of affected modules (e.g. "transformer,util").
    # When empty, all tests under com.zoho.dpaas are run.
    #
    unit_test_cmd = r'''
set -e
echo "━━━ Gate 4/4: Unit tests ━━━"

test -f dpaas_test.jar || { echo "❌ dpaas_test.jar not found"; exit 1; }
test -f dpaas.jar       || { echo "❌ dpaas.jar not found"; exit 1; }

# ── Setup resources in DPAAS_HOME ──
mkdir -p "$DPAAS_HOME/zdpas/spark/resources"
mkdir -p "$DPAAS_HOME/zdpas/spark/conf"
cp -r ./resources/*      "$DPAAS_HOME/zdpas/spark/resources/" 2>/dev/null || true
cp -r ./test/resources/* "$DPAAS_HOME/zdpas/spark/resources/" 2>/dev/null || true
if [[ -n "$BUILD_FILE_HOME" ]] && [[ -f "$BUILD_FILE_HOME/datastore.json" ]]; then
    cp "$BUILD_FILE_HOME/datastore.json" \
       "$DPAAS_HOME/zdpas/spark/resources/datastore.json"
fi
cp ./resources/log4j.properties \
    "$DPAAS_HOME/zdpas/spark/conf/log4j-local.properties" 2>/dev/null || true

# ── Build ScalaTest -w / -s arguments from SATURN_TEST_MODULES ──
TEST_ARGS=""
if [[ -z "$SATURN_TEST_MODULES" ]]; then
    TEST_ARGS="-w com.zoho.dpaas"
    echo "  Running ALL tests (no SATURN_TEST_MODULES set)"
else
    echo "  Running tests for: $SATURN_TEST_MODULES"
    IFS=',' read -ra MODULES <<< "$SATURN_TEST_MODULES"
    for module in "${MODULES[@]}"; do
        module=$(echo "$module" | tr -d ' ')
        module_lower=$(echo "$module" | tr '[:upper:]' '[:lower:]')
        case "$module_lower" in
            # ═══ MAIN PACKAGES ═══
            transformer|transforms) TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.transformer" ;;
            dataframe|io)           TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.dataframe" ;;
            storage)                TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.storage" ;;
            util|utils)             TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.util" ;;
            context)                TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.context" ;;
            query)                  TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.query" ;;
            widgets)                TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.widgets" ;;
            udf)                    TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.udf" ;;
            callback)               TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.callback" ;;
            common)                 TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.common" ;;
            datatype)               TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.datatype" ;;
            parquet)                TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.parquet" ;;
            redis)                  TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.redis" ;;
            ruleset)                TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.ruleset" ;;
            executors)              TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.executors" ;;
            all)                    TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas" ;;
            # ═══ TRANSFORMER SUITES ═══
            join)        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDJoinSuite" ;;
            union)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDUnionSuite" ;;
            append)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDAppendSuite" ;;
            merge)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDMergeSuite" ;;
            filter)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDFilterSuite" ;;
            select)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDSelectSuite" ;;
            drop)        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDDropSuite" ;;
            derive)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDDeriveSuite" ;;
            convert)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDConvertSuite" ;;
            trim)        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDTrimSuite" ;;
            rename)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDRenameSuite" ;;
            sort)        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDSortSuite" ;;
            group)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDGroupSuite" ;;
            pivot)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDPivotSuite" ;;
            unpivot)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDUnpivotSuite" ;;
            dedup|deduplicate) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDDeDuplicateSuite" ;;
            fill|fillcells)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDFillCellsSuite" ;;
            export)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDExportSuite" ;;
            # ═══ DATAFRAME IO SUITES ═══
            csv|csvreader) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.CSVReaderSuite" ;;
            excel|excelio) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZExcelIOSuite" ;;
            json|jsonio)   TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZJsonIOSuite" ;;
            xml|xmlio)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZXmlIOSuite" ;;
            parquetio)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZParquetIOSuite" ;;
            # ═══ FULL PACKAGE (com.zoho.* passthrough) ═══
            com.zoho.*)    TEST_ARGS="$TEST_ARGS -w $module" ;;
            # ═══ EXPLICIT SUITE NAME (*Suite) ═══
            *Suite)
                for pkg in transformer dataframe storage util context query \
                           widgets udf callback common datatype parquet redis; do
                    if [[ -f "./test/source/com/zoho/dpaas/$pkg/${module}.scala" ]]; then
                        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.${pkg}.${module}"
                        break
                    fi
                done
                ;;
            # ═══ DEFAULT: treat as package suffix ═══
            *)  TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.${module}" ;;
        esac
    done
fi

echo "  Test args: $TEST_ARGS"

java \
    -cp "./dpaas_test.jar:./dpaas.jar:./resources:./test/resources:$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/app_blue/ExpParser.jar:$DPAAS_HOME/zdpas/spark/lib/*" \
    -Xmx3g \
    -Dserver.dir="$DPAAS_HOME/zdpas/spark" \
    org.scalatest.tools.Runner \
    -Dlog4j.configuration="file:$DPAAS_HOME/zdpas/spark/conf/log4j-local.properties" \
    -R ./dpaas_test.jar \
    $TEST_ARGS \
    -oC \
    -u unit_tests \
    -f test.out 2>>err.log

echo "✅ Tests passed"
'''

    return [
        GateDef(
            name="setup",
            description="Extract dpaas.tar.gz → populate DPAAS_HOME runtime",
            command=setup_cmd,
            retryable=False,  # tar extraction failure is not a code problem
        ),
        GateDef(
            name="compile",
            description="Joint-compile Java+Scala sources → dpaas.jar",
            command=compile_cmd,
            retryable=True,
        ),
        GateDef(
            name="build-test-jar",
            description="Compile test sources → dpaas_test.jar",
            command=build_test_cmd,
            retryable=True,
        ),
        GateDef(
            name="unit-tests",
            description="Run ScalaTest for affected modules",
            command=unit_test_cmd,
            retryable=True,
        ),
    ]


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
