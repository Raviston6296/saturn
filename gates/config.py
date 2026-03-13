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

    For zdpas: Uses validate_gates.sh for compilation and testing.
    """
    gates, project_type = _detect_gates(workspace)
    risk = _default_risk()

    if gates:
        print(f"  🔍 Detected project type: {project_type}")
        for g in gates:
            print(f"     • {g.name}: {g.command[:60]}...")
    else:
        print("  ⚠️  Could not detect project type — no default gates")

    return SaturnRepoConfig(
        gates=GatesConfig(gates=gates),
        rules=RulesConfig(),
        risk=risk,
        has_config=False,
    )


def _detect_gates(workspace: Path) -> tuple[list[GateDef], str]:
    """
    Detect project type and return appropriate gates.

    For zdpas (Scala/Java with Ant): Uses Saturn's validate_gates.sh
    which handles the complex compilation order and isolated DPAAS environment.
    """
    import os
    saturn_home = os.environ.get("SATURN_HOME", "/home/gitlab-runner/saturn")

    # ════════════════════════════════════════════════════════════════════════
    # ZDPAS Detection: Look for zdpas-specific markers
    # ════════════════════════════════════════════════════════════════════════
    is_zdpas = (
        (workspace / "build" / "ant.properties").exists() or
        (workspace / "source" / "com" / "zoho" / "dpaas").exists() or
        ((workspace / "build.xml").exists() and (workspace / "source").exists() and (workspace / "test").exists())
    )

    if is_zdpas:
        return _get_zdpas_gates(workspace, saturn_home), "ZDPAS (Scala/Java)"

    # ════════════════════════════════════════════════════════════════════════
    # Other project types (fallback)
    # ════════════════════════════════════════════════════════════════════════

    # sbt (Scala)
    if (workspace / "build.sbt").exists():
        return [
            GateDef("compile", "Compile Scala project", "sbt compile", retryable=True),
            GateDef("fast-tests", "Run unit tests", "sbt test", retryable=True),
        ], "Scala (sbt)"

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

    # Python
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


def _get_zdpas_gates(workspace: Path, saturn_home: str) -> list[GateDef]:
    """
    Get gates for ZDPAS project matching validate_gates.sh exactly.

    This ensures:
    - Isolated DPAAS_HOME (no conflict with GitLab runner)
    - Correct compilation order (Scala + Java joint compile)
    - 140 suite shortcuts for module-based testing
    - Full classpath including resources and build output
    """

    # Gate 1: Compile (joint Java+Scala → dpaas.jar)
    compile_cmd = '''
set -e
echo "📦 Compiling dpaas.jar (ZDPAS)..."

# Find sources (matching CI/CD exactly)
find ./source -name "*.java" -type f | grep -v -E '^./source/(Main|Test)\\.' > all_java.txt 2>/dev/null || touch all_java.txt
find ./source -name "*.scala" -type f | grep -v -E "^./source/(Main|Test|Generate)" > scala_files.txt
cat all_java.txt scala_files.txt > all_sources.txt

JAVA_COUNT=$(wc -l < all_java.txt | tr -d ' ')
SCALA_COUNT=$(wc -l < scala_files.txt | tr -d ' ')
echo "  Found $JAVA_COUNT Java and $SCALA_COUNT Scala files"

mkdir -p compiled_classes

# Step 1: Joint compile with scalac (matching CI/CD)
echo "  Step 1: scalac joint compilation..."
scalac -cp "$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/lib/*" -J-Xmx2g -d compiled_classes @all_sources.txt

# Step 2: Compile Java with javac (matching CI/CD)
if [[ $JAVA_COUNT -gt 0 ]]; then
    echo "  Step 2: javac compilation..."
    javac -cp "compiled_classes:$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/lib/*" -sourcepath ./source -d compiled_classes @all_java.txt
fi

# Step 3: Create JAR with resources (matching CI/CD)
echo "  Step 3: Creating JAR..."
mkdir -p $DPAAS_HOME/zdpas/spark/app_blue
jar cf "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar" -C compiled_classes .

# Add main resources
if [[ -d "./resources" ]]; then
    jar uf "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar" -C ./resources . 2>/dev/null || true
    echo "  Added resources: configuration.properties, datatypes.json, etc."
fi

# Create local copy for test compilation
cp "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar" ./dpaas.jar

rm -rf compiled_classes all_java.txt scala_files.txt all_sources.txt

ls -l "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar"
echo "✅ Compile done"
'''

    # Gate 2: Build test JAR
    build_test_cmd = '''
set -e
echo "🧪 Building test JAR..."

# Find test sources (matching CI/CD)
find ./test/source -name "*.scala" -type f > source_test.txt
TEST_COUNT=$(wc -l < source_test.txt | tr -d ' ')
echo "  Found $TEST_COUNT test files"

mkdir -p test_compiled_classes

# Compile test sources (matching CI/CD)
scalac -cp "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar:$DPAAS_HOME/zdpas/spark/app_blue/ExpParser.jar:$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/lib/*" -J-Xmx2g -d test_compiled_classes @source_test.txt

# Create test JAR
jar cf dpaas_test.jar -C test_compiled_classes .

# Add test resources
if [[ -d "./test/resources" ]]; then
    jar uf dpaas_test.jar -C ./test/resources . 2>/dev/null || true
    echo "  Added test resources"
fi

rm -rf test_compiled_classes source_test.txt

ls -l dpaas_test.jar
echo "✅ Test JAR built"
'''

    # Gate 3: Unit tests (with full 140 suite shortcuts from validate_gates.sh)
    unit_test_cmd = '''
set -e
echo "🧪 Running unit tests..."

test -f dpaas_test.jar || { echo "❌ dpaas_test.jar not found"; exit 1; }

# Setup resources (matching CI/CD)
mkdir -p "$DPAAS_HOME/zdpas/spark/resources"
mkdir -p "$DPAAS_HOME/zdpas/spark/conf"
cp -r ./resources/* "$DPAAS_HOME/zdpas/spark/resources/" 2>/dev/null || true
cp -r ./test/resources/* "$DPAAS_HOME/zdpas/spark/resources/" 2>/dev/null || true
cp "$BUILD_FILE_HOME/datastore.json" "$DPAAS_HOME/zdpas/spark/resources/datastore.json" 2>/dev/null || true
cp ./resources/log4j.properties "$DPAAS_HOME/zdpas/spark/conf/log4j-local.properties" 2>/dev/null || true

# ═══════════════════════════════════════════════════════════════════════════
# Build test arguments based on SATURN_TEST_MODULES
# Includes all 140 suite shortcuts from validate_gates.sh
# ═══════════════════════════════════════════════════════════════════════════
TEST_ARGS=""
if [[ -z "$SATURN_TEST_MODULES" ]]; then
    TEST_ARGS="-w com.zoho.dpaas"
    echo "  Running ALL tests"
else
    echo "  Running tests for: $SATURN_TEST_MODULES"
    IFS=',' read -ra MODULES <<< "$SATURN_TEST_MODULES"
    for module in "${MODULES[@]}"; do
        module=$(echo "$module" | tr -d ' ')
        module_lower=$(echo "$module" | tr '[:upper:]' '[:lower:]')
        
        case "$module_lower" in
            # ═══ MAIN MODULES ═══
            transformer|transforms)  TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.transformer" ;;
            dataframe|io)            TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.dataframe" ;;
            storage)                 TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.storage" ;;
            util|utils)              TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.util" ;;
            context)                 TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.context" ;;
            query)                   TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.query" ;;
            widgets)                 TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.widgets" ;;
            udf)                     TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.udf" ;;
            callback)                TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.callback" ;;
            common)                  TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.common" ;;
            datatype)                TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.datatype" ;;
            parquet)                 TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.parquet" ;;
            import)                  TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.import" ;;
            redis)                   TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.redis" ;;
            ruleset)                 TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.ruleset" ;;
            executors)               TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.executors" ;;
            all)                     TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas" ;;
            
            # ═══ TRANSFORMER SUITES (68) ═══
            join)           TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDJoinSuite" ;;
            union)          TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDUnionSuite" ;;
            append)         TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDAppendSuite" ;;
            merge)          TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDMergeSuite" ;;
            filter)         TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDFilterSuite" ;;
            select)         TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDSelectSuite" ;;
            drop)           TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDDropSuite" ;;
            hide)           TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDHideSuite" ;;
            derive)         TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDDeriveSuite" ;;
            convert)        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDConvertSuite" ;;
            settype)        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDSetTypeSuite" ;;
            mlderive)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDMLDeriveSuite" ;;
            group)          TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDGroupSuite" ;;
            pivot)          TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDPivotSuite" ;;
            unpivot)        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDUnpivotSuite" ;;
            bucket)         TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDBucketSuite" ;;
            split|splitdelim) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDSplitDelimiterSuite" ;;
            splitchar)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDSplitCharSuite" ;;
            splitconst)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDSplitConstantSuite" ;;
            splitpos)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDSplitPositionSuite" ;;
            splitregex)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDSplitRegExSuite" ;;
            combine)        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDCombineSuite" ;;
            flatten)        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDFlattenSuite" ;;
            flattenrow)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDFlattenAsRowSuite" ;;
            flattencol)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDFlattenAsColumnsSuite" ;;
            nest)           TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDNestSuite" ;;
            unnest)         TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDUnnestSuite" ;;
            unnestenrich)   TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDUnnestEnrichedSuite" ;;
            extract|extractconst) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDExtractConstantSuite" ;;
            extractdelim)   TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDExtractDelimiterSuite" ;;
            extractpos)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDExtractPositionSuite" ;;
            extractregex)   TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDExtractRegExSuite" ;;
            extractdate)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDExtractDateSuite" ;;
            extractemail)   TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDExtractEmailSuite" ;;
            extracturl)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDExtractUrlSuite" ;;
            extractquality) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDExtractQualitySuite" ;;
            replace|replaceconst) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDReplaceConstantSuite" ;;
            replacedelim)   TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDReplaceDelimiterSuite" ;;
            replacepos)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDReplacePositionSuite" ;;
            replaceregex)   TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDReplaceRegExSuite" ;;
            replaceconstraint) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDReplaceConstraintSuite" ;;
            countconst)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDCountConstantSuite" ;;
            countdelim)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDCountDelimiterSuite" ;;
            countregex)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDCountRegExSuite" ;;
            trim)           TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDTrimSuite" ;;
            case|changecase) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDChangeCaseSuite" ;;
            truncate)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDTruncateSuite" ;;
            date|dateformat) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDDateFormatSuite" ;;
            dateunify)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDDateUnifierSuite" ;;
            number|numberformat) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDNumberFormatSuite" ;;
            duration)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDDurationFormatSuite" ;;
            roundoff)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDRoundOffSuite" ;;
            rename)         TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDRenameSuite" ;;
            move)           TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDMoveSuite" ;;
            header)         TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDHeaderSuite" ;;
            duplicate|dupcol) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDDuplicateColumnSuite" ;;
            internal)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDInternalColumnsSuite" ;;
            dedup|deduplicate) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDDeDuplicateSuite" ;;
            deduppreview)   TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDDeduplicatePreviewSuite" ;;
            cluster|clustermerge) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDClusterNMergeSuite" ;;
            fill|fillcells) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDFillCellsSuite" ;;
            dataaccuracy)   TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDDataTypeAccuracySuite" ;;
            privacy|setprivacy) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDSetPrivacySuite" ;;
            export)         TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDExportSuite" ;;
            widget)         TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDWidgetSuite" ;;
            queryrunner)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDQueryRunnerSuite" ;;
            sort)           TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.ZDSortSuite" ;;
            
            # ═══ DATAFRAME IO SUITES (15) ═══
            csv|csvreader)  TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.CSVReaderSuite" ;;
            csvio)          TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZCsvIOSuite" ;;
            excel|excelio)  TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZExcelIOSuite" ;;
            parquetio)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZParquetIOSuite" ;;
            parquetread)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.PRAQUETReaderSuite" ;;
            json|jsonio)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZJsonIOSuite" ;;
            xml|xmlio)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZXmlIOSuite" ;;
            text|textio)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZTextIOSuite" ;;
            html|htmlio)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZHtmlIOSuite" ;;
            zip|zipio)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZZipIOSuite" ;;
            dfio)           TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZDataFrameIOSuite" ;;
            abstractio)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZAbstractDataFrameIOSuite" ;;
            sparkrw)        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.dataframe.ZSparkRWConstantsSuite" ;;
            
            # ═══ STORAGE SUITES (8) ═══
            dfs|dfsstorage) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.storage.ZDDFSStorageSuite" ;;
            hdfs|hdfsstorage) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.storage.ZDHDFSStorageSuite" ;;
            local|localstorage) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.storage.ZDLocalStorageSuite" ;;
            storagefactory) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.storage.ZStorageFactorySuite" ;;
            fspath)         TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.storage.ZDFSPathParsingSuite" ;;
            fspathparse)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.storage.ZFileSystemPathParsingSuite" ;;
            storagepath)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.storage.ZStoragePathParsingSuite" ;;
            
            # ═══ UTIL SUITES (6) ═══
            zdutil)         TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.util.ZDUtilSuite" ;;
            pattern|patternutil) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.util.PatternTextRegExUtilSuite" ;;
            relfilter)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.util.RelativeFilterUtilSuite" ;;
            sqlbuilder)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.util.SparkSqlQueryBuilderSuite" ;;
            ruleutil)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.util.RuleMigratorUtilSuite" ;;
            joinpotential)  TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.util.JoinPotentialUtilSuite" ;;
            
            # ═══ CONTEXT SUITES (5) ═══
            contexttest)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.context.ContextSuite" ;;
            jobcontext)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.context.JobContextSuite" ;;
            rulecontext)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.context.RuleContextSuite" ;;
            rulesetcontext) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.context.RuleSetContextSuite" ;;
            
            # ═══ QUERY SUITES (3) ═══
            sparkselect)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.query.SparkSelectQuerySuite" ;;
            dag)            TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.query.DAGSuite" ;;
            logical)        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.query.LogicalExpressionParserSuite" ;;
            
            # ═══ WIDGET SUITES (3) ═══
            widgetgen)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.widgets.WidgetGeneratorSuite" ;;
            widgetsuite)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.widgets.WidgetSuite" ;;
            numwidget)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.widgets.NumericWidgetsSuite" ;;
            
            # ═══ UDF SUITES (7) ═══
            datatypeudf)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.udf.DataTypeValidationUdfsSuite" ;;
            datearith)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.udf.DateArithmeticUdfsSuite" ;;
            dateextract)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.udf.DateExtractionUdfsSuite" ;;
            jsonudf)        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.udf.JsonUdfsSuite" ;;
            logicaludf)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.udf.LogicalUdfsSuite" ;;
            numericudf)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.udf.NumericUdfsSuite" ;;
            textconv)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.udf.TextConversionUdfsSuite" ;;
            
            # ═══ CALLBACK SUITES (3) ═══
            callbacksuite)  TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.callback.CallBackHandlerSuite" ;;
            webcallback)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.callback.WebCallBackHandlerSuite" ;;
            response)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.callback.DPAASResponseSuite" ;;
            
            # ═══ COMMON SUITES (4) ═══
            dsmodel)        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.common.ZDDSModelUtilSuite" ;;
            colmodel)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.common.ZDColumnModelUtilSuite" ;;
            dfutil)         TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.common.ZDDataFrameUtilSuite" ;;
            parserutil)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.common.ParserUtilSuite" ;;
            
            # ═══ DATATYPE SUITES (6) ═══
            datatypeutil)   TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.datatype.DataTypeUtilSuite" ;;
            datematcher)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.datatype.DateMatcherSuite" ;;
            durationmatch)  TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.datatype.DurationMatcherSuite" ;;
            patternmatch)   TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.datatype.PatternMatcherSuite" ;;
            primitive)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.datatype.PrimitiveDataTypesSuite" ;;
            sqltypes)       TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.datatype.SqlTypesSuite" ;;
            
            # ═══ PARQUET SUITES (2) ═══
            parquetprops)   TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.parquet.ZParquetPropertiesSuite" ;;
            parquetiosuite) TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.parquet.ZDParquetIOSuite" ;;
            
            # ═══ IMPORT SUITES (9) ═══
            csvimport)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.import.CsvImportSuite" ;;
            excelimport)    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.import.ExcelImportSuite" ;;
            jsonimport)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.import.JsonImportSuite" ;;
            xmlimport)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.import.XmlImportSuite" ;;
            htmlimport)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.import.HtmlImportSuite" ;;
            textimport)     TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.import.TextImportSuite" ;;
            tsvimport)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.import.TsvImportSuite" ;;
            zipimport)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.import.ZipImportJobLevelSuite" ;;
            
            # ═══ REDIS SUITES (1) ═══
            redisutil)      TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.redis.RedisUtilSuite" ;;
            
            # ═══ FULL PACKAGE ═══
            com.zoho.*)     TEST_ARGS="$TEST_ARGS -w $module" ;;
            
            # ═══ SUITE NAME (starts with uppercase, ends with Suite) ═══
            *Suite)
                # Search for suite in known packages
                for pkg in transformer dataframe storage util context query widgets udf callback common datatype parquet import redis; do
                    if [[ -f "./test/source/com/zoho/dpaas/$pkg/${module}.scala" ]]; then
                        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.${pkg}.${module}"
                        break
                    fi
                done
                # Default to transformer if not found
                if [[ ! "$TEST_ARGS" =~ "$module" ]]; then
                    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.${module}"
                fi
                ;;
            
            # ═══ ZD* pattern (e.g., ZDJoin → ZDJoinSuite) ═══
            ZD*)
                TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.${module}Suite"
                ;;
            
            # ═══ DEFAULT: treat as package ═══
            *)
                TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.${module}"
                ;;
        esac
    done
fi

echo "  Test args: $TEST_ARGS"

# Run ScalaTest (matching CI/CD classpath exactly)
java -cp "./dpaas_test.jar:./dpaas.jar:./resources:./test/resources:build/ZDPAS/output/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/app_blue/ExpParser.jar:$DPAAS_HOME/zdpas/spark/lib/*" \\
    -Xmx3g \\
    -Dserver.dir="$DPAAS_HOME/zdpas/spark" \\
    org.scalatest.tools.Runner \\
    -Dlog4j.configuration="file:$DPAAS_HOME/zdpas/spark/conf/log4j-local.properties" \\
    -R ./dpaas_test.jar \\
    $TEST_ARGS \\
    -oC \\
    -u unit_tests \\
    -f test.out 2>>err.log

echo "✅ Tests passed"
'''

    return [
        GateDef(
            name="compile",
            description="Compile dpaas.jar (Scala + Java)",
            command=compile_cmd,
            retryable=True,
        ),
        GateDef(
            name="build-test-jar",
            description="Build dpaas_test.jar",
            command=build_test_cmd,
            retryable=True,
        ),
        GateDef(
            name="unit-tests",
            description="Run ScalaTest (module-based)",
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
