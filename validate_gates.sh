#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Saturn Gates Validation Script for ZDPAS
# ═══════════════════════════════════════════════════════════════════════════
#
# This script validates that the Saturn gates.yaml compilation commands
# match the zdpas CI/CD pipeline exactly.
#
# Run this on the Saturn runner VM to verify gates work correctly:
#   ./validate_gates.sh                           # Run all tests
#   ./validate_gates.sh . transformer             # Run transformer tests
#   ./validate_gates.sh . dataframe,util          # Run dataframe and util tests
#   ./validate_gates.sh . ZDAppendSuite           # Run specific test suite
#   ./validate_gates.sh . ZDJoinSuite,ZDUnionSuite # Run multiple suites
#
# Updated: 2026-03-13 - Enhanced for zdpas repo structure
#
# ZDPAS Package Structure:
#   source/com/zoho/dpaas/
#     ├── callback/      - Callback handlers
#     ├── common/        - Common utilities and models
#     ├── context/       - Job and rule contexts
#     ├── dataframe/     - DataFrame IO operations
#     ├── datatype/      - Data type handling
#     ├── dfs/           - DFS client interfaces
#     ├── dfsimpl/       - DFS implementations
#     ├── exception/     - Exception classes
#     ├── importutil/    - Import utilities
#     ├── job/           - Job definitions
#     ├── logging/       - Logging utilities
#     ├── metrics/       - Metrics collection
#     ├── migrator/      - Migration utilities
#     ├── parquet/       - Parquet handling
#     ├── parser/        - Date/format parsers
#     ├── pdc/           - PDC collectors
#     ├── processor/     - Data processors
#     ├── query/         - Query builders
#     ├── redis/         - Redis utilities
#     ├── ruleset/       - Rule set handling
#     ├── sparkutil/     - Spark utilities
#     ├── storage/       - Storage abstraction
#     ├── transformer/   - Transform operations (largest module)
#     ├── udaf/          - User-defined aggregate functions
#     ├── udf/           - User-defined functions
#     ├── util/          - General utilities
#     ├── widgets/       - Widget generation
#     ├── writer/        - Data writers
#     └── zdfs/          - ZDFS implementation
#
# ═══════════════════════════════════════════════════════════════════════════

set -e

# ─────────────────────────────────────────────────────────────────────────────
# Color codes for output
# ─────────────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ─────────────────────────────────────────────────────────────────────────────
# Parse Arguments
# ─────────────────────────────────────────────────────────────────────────────
WORKSPACE="${1:-.}"
TEST_MODULES="${2:-}"  # Optional: comma-separated list of modules or suite names
SKIP_COMPILE="${SKIP_COMPILE:-false}"  # Set to true to skip compilation

echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}🔍 Saturn Gates Validation for ZDPAS${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"

if [[ -n "$TEST_MODULES" ]]; then
    echo -e "  ${YELLOW}📦 Test modules: $TEST_MODULES${NC}"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Check Environment
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "━━━ Checking environment ━━━"

if [[ -z "$DPAAS_HOME" ]]; then
    echo -e "${RED}❌ DPAAS_HOME not set${NC}"
    exit 1
fi
echo -e "  ${GREEN}✅ DPAAS_HOME: $DPAAS_HOME${NC}"

# Use GitLab runner's DPAAS for resources if available (has proper config files)
RUNNER_DPAAS_HOME="${RUNNER_DPAAS_HOME:-/opt/dpaas}"
if [[ -d "$RUNNER_DPAAS_HOME/zdpas/spark/resources" ]]; then
    echo -e "  ${GREEN}✅ RUNNER_DPAAS_HOME: $RUNNER_DPAAS_HOME (for resources)${NC}"
    USE_RUNNER_RESOURCES=true
else
    echo -e "  ${YELLOW}⚠️  RUNNER_DPAAS_HOME not found, using DPAAS_HOME for resources${NC}"
    USE_RUNNER_RESOURCES=false
fi

if [[ -z "$BUILD_FILE_HOME" ]]; then
    echo -e "  ${YELLOW}⚠️  BUILD_FILE_HOME not set (using default)${NC}"
    BUILD_FILE_HOME="/home/test/git-runner/ref"
fi
echo -e "  ${GREEN}✅ BUILD_FILE_HOME: $BUILD_FILE_HOME${NC}"

echo ""
echo "━━━ Checking tools ━━━"
scalac -version
java -version 2>&1 | head -1
jar --version 2>&1 | head -1 || echo "  jar available"

# ─────────────────────────────────────────────────────────────────────────────
# Test Directory Setup
# ─────────────────────────────────────────────────────────────────────────────
cd "$WORKSPACE"
echo ""
echo "━━━ Workspace: $(pwd) ━━━"

# Verify this is a zdpas repo
if [[ ! -d "./source/com/zoho/dpaas" ]]; then
    echo -e "${RED}❌ Not a zdpas repository (source/com/zoho/dpaas not found)${NC}"
    exit 1
fi
echo -e "  ${GREEN}✅ ZDPAS repository structure verified${NC}"

# ─────────────────────────────────────────────────────────────────────────────
# Gate 1: COMPILE (matching CI/CD build_dpaas_jar_from_cache)
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$SKIP_COMPILE" != "true" ]]; then

    # Check if we can skip compilation (jar exists and is newer than sources)
    NEED_COMPILE=true
    if [[ -f "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar" ]]; then
        JAR_TIME=$(stat -c %Y "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar" 2>/dev/null || stat -f %m "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar" 2>/dev/null)
        NEWEST_SOURCE=$(find ./source -name "*.scala" -o -name "*.java" -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1)

        if [[ -n "$JAR_TIME" ]] && [[ -n "$NEWEST_SOURCE" ]] && [[ "$JAR_TIME" -gt "$NEWEST_SOURCE" ]]; then
            echo ""
            echo -e "${YELLOW}⏭️  Skipping compilation - dpaas.jar is up to date${NC}"
            echo "   (jar: $(date -d @$JAR_TIME 2>/dev/null || date -r $JAR_TIME), newest source: $(date -d @$NEWEST_SOURCE 2>/dev/null || date -r $NEWEST_SOURCE))"
            NEED_COMPILE=false

            # Ensure local copy exists
            if [[ ! -f "./dpaas.jar" ]]; then
                cp "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar" ./dpaas.jar
            fi
        fi
    fi

    if [[ "$NEED_COMPILE" == "true" ]]; then
        echo ""
        echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"
        echo -e "${BLUE}🚧 Gate 1: COMPILE${NC}"
        echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"

    echo ""
    echo "━━━ Step 1/4: Finding source files (matching CI/CD) ━━━"

    # This matches the CI/CD command exactly:
    # find ./source -name "*.java" -type f | grep -v -E '^./source/(Main|Test)\.' > all_java.txt
    # find ./source -name "*.scala" -type f | grep -v -E "^./source/(Main|Test|Generate)" > scala_files.txt
    find ./source -name "*.java" -type f \
        | grep -v -E '^./source/(Main|Test)\.' > all_java.txt 2>/dev/null || touch all_java.txt

    find ./source -name "*.scala" -type f \
        | grep -v -E "^./source/(Main|Test|Generate)" > scala_files.txt

    # Combine for joint compilation
    cat all_java.txt scala_files.txt > all_sources.txt

    JAVA_COUNT=$(wc -l < all_java.txt | tr -d ' ')
    SCALA_COUNT=$(wc -l < scala_files.txt | tr -d ' ')
    TOTAL_COUNT=$(wc -l < all_sources.txt | tr -d ' ')
    echo "  Found $JAVA_COUNT Java and $SCALA_COUNT Scala files, $TOTAL_COUNT total"

    # Show package breakdown
    echo ""
    echo "  Package breakdown:"
    for pkg in transformer common dataframe storage util parser context; do
        count=$(grep -c "source/com/zoho/dpaas/$pkg/" all_sources.txt 2>/dev/null || echo "0")
        echo "    - $pkg: $count files"
    done

    echo ""
    echo "━━━ Step 2/4: Joint-compiling Java and Scala with scalac (matching CI/CD) ━━━"

    mkdir -p compiled_classes

    # This matches the CI/CD command exactly:
    # scalac -cp "$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/lib/*" -J-Xmx2g -d compiled_classes @all_sources.txt
    echo "  Running: scalac -cp \$DPAAS_HOME/zdpas/spark/jars/*:\$DPAAS_HOME/zdpas/spark/lib/* -J-Xmx2g -d compiled_classes @all_sources.txt"

    scalac -cp "$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/lib/*" \
        -J-Xmx2g \
        -d compiled_classes \
        @all_sources.txt

    echo -e "  ${GREEN}✅ Joint Scala+Java compilation done${NC}"

    echo ""
    echo "━━━ Step 3/4: Compiling Java sources with javac (matching CI/CD) ━━━"

    # This matches the CI/CD command exactly:
    # javac -cp "compiled_classes:$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/lib/*" -sourcepath ./source -d compiled_classes @all_java.txt
    if [[ $JAVA_COUNT -gt 0 ]]; then
        echo "  Running: javac -cp compiled_classes:\$DPAAS_HOME/... -sourcepath ./source -d compiled_classes @all_java.txt"
        javac -cp "compiled_classes:$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/lib/*" \
            -sourcepath ./source \
            -d compiled_classes \
            @all_java.txt
        echo -e "  ${GREEN}✅ Java compilation done${NC}"
    else
        echo "  No Java files to compile"
    fi

    # Verify critical classes exist
    echo ""
    echo "  Verifying critical classes..."
    CRITICAL_CLASSES=(
        "com/zoho/dpaas/parser/dateparser/ZDDateParser.class"
        "com/zoho/dpaas/common/parser/ParserUtil.class"
        "com/zoho/dpaas/transformer/AbstractTransform.class"
        "com/zoho/dpaas/sparkutil/ZDSparkUtil.class"
    )
    for cls in "${CRITICAL_CLASSES[@]}"; do
        if [[ -f "compiled_classes/$cls" ]]; then
            echo -e "    ${GREEN}✅ $cls${NC}"
        else
            echo -e "    ${YELLOW}⚠️ $cls not found${NC}"
        fi
    done

    echo ""
    echo "━━━ Step 4/4: Creating JAR with resources (matching CI/CD) ━━━"

    # Ensure the target directory exists
    mkdir -p "$DPAAS_HOME/zdpas/spark/app_blue"

    # This matches the CI/CD commands exactly:
    # jar cf $DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar -C compiled_classes .
    # jar uf $DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar -C ./resources . 2>/dev/null || echo "No main resources to add"

    # First create local jar, then copy (avoids permission issues with direct write)
    jar cf ./dpaas.jar -C compiled_classes .

    # Add main resources (configs, properties, etc.)
    if [[ -d "./resources" ]]; then
        jar uf ./dpaas.jar -C ./resources . 2>/dev/null || echo "  No main resources to add"
        echo "  Added resources: configuration.properties, datatypes.json, function_types.json, etc."
    fi

    # Copy to DPAAS_HOME
    cp ./dpaas.jar "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar"

    echo -e "  ${GREEN}✅ JAR created: $DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar${NC}"
    ls -lh "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar"
    echo -e "  ${GREEN}✅ Local copy: ./dpaas.jar${NC}"

    # Cleanup
    rm -rf compiled_classes all_java.txt scala_files.txt all_sources.txt

    echo ""
    echo -e "${GREEN}✅ COMPILE gate passed${NC}"
    fi  # End of NEED_COMPILE check
else
    echo ""
    echo -e "${YELLOW}⏭️  Skipping COMPILE gate (SKIP_COMPILE=true)${NC}"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Gate 2: BUILD TEST JAR (matching CI/CD build_test_jar / build_dpaas_jar_from_cache)
# ─────────────────────────────────────────────────────────────────────────────
if [[ "$SKIP_COMPILE" != "true" ]]; then

    # Check if we can skip test JAR build (jar exists and is newer than test sources)
    NEED_TEST_COMPILE=true
    if [[ -f "./dpaas_test.jar" ]]; then
        JAR_TIME=$(stat -c %Y "./dpaas_test.jar" 2>/dev/null || stat -f %m "./dpaas_test.jar" 2>/dev/null)
        NEWEST_TEST=$(find ./test/source -name "*.scala" -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1)

        if [[ -n "$JAR_TIME" ]] && [[ -n "$NEWEST_TEST" ]] && [[ "$JAR_TIME" -gt "$NEWEST_TEST" ]]; then
            echo ""
            echo -e "${YELLOW}⏭️  Skipping test JAR build - dpaas_test.jar is up to date${NC}"
            NEED_TEST_COMPILE=false
        fi
    fi

    if [[ "$NEED_TEST_COMPILE" == "true" ]]; then
        echo ""
        echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"
        echo -e "${BLUE}🚧 Gate 2: BUILD TEST JAR${NC}"
        echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"

    echo ""
    echo "━━━ Finding test sources (matching CI/CD) ━━━"

    # This matches the CI/CD command exactly:
    # find ./test/source -name "*.scala" -type f > source_test.txt
    find ./test/source -name "*.scala" -type f > source_test.txt

    TEST_SCALA_COUNT=$(wc -l < source_test.txt | tr -d ' ')
    echo "  Found $TEST_SCALA_COUNT test Scala files"

    # Show test package breakdown
    echo ""
    echo "  Test package breakdown:"
    for pkg in transformer dataframe storage util context query widgets; do
        count=$(grep -c "test/source/com/zoho/dpaas/$pkg/" source_test.txt 2>/dev/null || echo "0")
        if [[ "$count" != "0" ]]; then
            echo "    - $pkg: $count test files"
        fi
    done

    echo ""
    echo "━━━ Compiling test sources (matching CI/CD) ━━━"

    mkdir -p test_compiled_classes

    # This matches the CI/CD command exactly:
    # scalac -cp "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar:$DPAAS_HOME/zdpas/spark/app_blue/ExpParser.jar:$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/lib/*" -J-Xmx2g -d test_compiled_classes @source_test.txt
    echo "  Running: scalac with CI/CD classpath (using compiled dpaas.jar)..."

    scalac -cp "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar:$DPAAS_HOME/zdpas/spark/app_blue/ExpParser.jar:$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/lib/*" \
        -J-Xmx2g \
        -d test_compiled_classes \
        @source_test.txt

    echo -e "  ${GREEN}✅ Test compilation done${NC}"

    echo ""
    echo "━━━ Creating test JAR with resources (matching CI/CD) ━━━"

    # This matches the CI/CD commands exactly:
    # jar cf dpaas_test.jar -C test_compiled_classes .
    # jar uf dpaas_test.jar -C ./test/resources . 2>/dev/null || echo "No test resources to add"
    jar cf dpaas_test.jar -C test_compiled_classes .

    # Add test resources
    if [[ -d "./test/resources" ]]; then
        jar uf dpaas_test.jar -C ./test/resources . 2>/dev/null || echo "  No test resources to add"
        echo "  Added test resources: Test.csv, sample.csv, automation/, import/, etc."
    fi

    echo -e "  ${GREEN}✅ Test JAR created: dpaas_test.jar${NC}"
    ls -lh dpaas_test.jar

    # Cleanup
    rm -rf test_compiled_classes source_test.txt

    echo ""
    echo -e "${GREEN}✅ BUILD TEST JAR gate passed${NC}"
    fi  # End of NEED_TEST_COMPILE check
else
    echo ""
    echo -e "${YELLOW}⏭️  Skipping BUILD TEST JAR gate (SKIP_COMPILE=true)${NC}"

    # Check if required jars exist when skipping compile
    if [[ ! -f "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar" ]]; then
        echo -e "${RED}❌ ERROR: dpaas.jar not found at $DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar${NC}"
        echo -e "${RED}   Cannot skip compile - jars don't exist yet.${NC}"
        echo -e "${RED}   Run without SKIP_COMPILE=true first to build the jars.${NC}"
        exit 1
    fi

    if [[ ! -f "./dpaas_test.jar" ]] && [[ ! -f "$DPAAS_HOME/zdpas/spark/app_blue/dpaas_test.jar" ]]; then
        echo -e "${RED}❌ ERROR: dpaas_test.jar not found${NC}"
        echo -e "${RED}   Cannot skip compile - test jar doesn't exist yet.${NC}"
        echo -e "${RED}   Run without SKIP_COMPILE=true first to build the jars.${NC}"
        exit 1
    fi

    echo -e "  ${GREEN}✅ Found existing jars${NC}"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Gate 3: UNIT TESTS (matching CI/CD unit_test)
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}🚧 Gate 3: UNIT TESTS${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"

# Check required jars exist
if [[ ! -f "./dpaas.jar" ]] && [[ ! -f "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar" ]]; then
    echo -e "${RED}❌ ERROR: dpaas.jar not found - compilation required${NC}"
    exit 1
fi

if [[ ! -f "./dpaas_test.jar" ]]; then
    echo -e "${RED}❌ ERROR: dpaas_test.jar not found - test compilation required${NC}"
    exit 1
fi

echo ""
echo "━━━ Setting up resources (matching CI/CD) ━━━"

# Setup resources directory
mkdir -p "$DPAAS_HOME/zdpas/spark/resources"
mkdir -p "$DPAAS_HOME/zdpas/spark/conf"

# Copy main resources from repo (required for ZDGlobalSettings)
if [[ -d "./resources" ]]; then
    cp -r ./resources/* "$DPAAS_HOME/zdpas/spark/resources/" 2>/dev/null
    echo "  ✅ Copied main resources"
else
    echo -e "  ${YELLOW}⚠️ ./resources directory not found${NC}"
fi

# Copy test resources
if [[ -d "./test/resources" ]]; then
    cp -r ./test/resources/* "$DPAAS_HOME/zdpas/spark/resources/" 2>/dev/null
    echo "  ✅ Copied test resources"
else
    echo -e "  ${YELLOW}⚠️ ./test/resources directory not found${NC}"
fi

# Ensure datastore.json exists (required for tests)
if [[ -f "$BUILD_FILE_HOME/datastore.json" ]]; then
    cp "$BUILD_FILE_HOME/datastore.json" "$DPAAS_HOME/zdpas/spark/resources/datastore.json"
    echo "  ✅ Copied datastore.json from BUILD_FILE_HOME"
elif [[ -f "./resources/datastore.json" ]]; then
    cp "./resources/datastore.json" "$DPAAS_HOME/zdpas/spark/resources/datastore.json"
    echo "  ✅ Copied datastore.json from ./resources"
elif [[ -f "./test/resources/datastore.json" ]]; then
    cp "./test/resources/datastore.json" "$DPAAS_HOME/zdpas/spark/resources/datastore.json"
    echo "  ✅ Copied datastore.json from ./test/resources"
else
    echo -e "  ${YELLOW}⚠️ datastore.json not found - tests may fail${NC}"
fi

# Ensure log4j config exists
if [[ -f "./resources/log4j.properties" ]]; then
    cp "./resources/log4j.properties" "$DPAAS_HOME/zdpas/spark/conf/log4j-local.properties"
    echo "  ✅ Copied log4j.properties"
elif [[ -f "$BUILD_FILE_HOME/log4j.properties" ]]; then
    cp "$BUILD_FILE_HOME/log4j.properties" "$DPAAS_HOME/zdpas/spark/conf/log4j-local.properties"
    echo "  ✅ Copied log4j.properties from BUILD_FILE_HOME"
else
    # Create a minimal log4j config
    cat > "$DPAAS_HOME/zdpas/spark/conf/log4j-local.properties" << 'LOGEOF'
log4j.rootLogger=WARN, console
log4j.appender.console=org.apache.log4j.ConsoleAppender
log4j.appender.console.layout=org.apache.log4j.PatternLayout
log4j.appender.console.layout.ConversionPattern=%d{yyyy-MM-dd HH:mm:ss} %-5p %c{1}:%L - %m%n
LOGEOF
    echo "  ✅ Created default log4j.properties"
fi

# List what's in resources (for debugging)
echo ""
echo "  Resources directory contents:"
ls -la "$DPAAS_HOME/zdpas/spark/resources/" 2>/dev/null | head -10 || echo "  (empty)"
ls -la "$DPAAS_HOME/zdpas/spark/resources/datastore.json" 2>/dev/null || echo -e "  ${YELLOW}⚠️ datastore.json not found${NC}"

# Setup log4j config (matching CI/CD)
cp ./resources/log4j.properties "$DPAAS_HOME/zdpas/spark/conf/log4j-local.properties" 2>/dev/null || \
    cp build/ZDPAS/output/zdpas/spark/conf/log4j-local.properties "$DPAAS_HOME/zdpas/spark/conf/" 2>/dev/null || \
    echo -e "  ${YELLOW}⚠️ log4j config not found${NC}"

echo ""
echo "━━━ Building test arguments ━━━"

# ─────────────────────────────────────────────────────────────────────────────
# ZDPAS Module to Package Mapping (based on actual repo structure)
# ─────────────────────────────────────────────────────────────────────────────
declare -A MODULE_MAP=(
    # Main test modules (matching test/source/com/zoho/dpaas/*)
    ["transformer"]="com.zoho.dpaas.transformer"
    ["dataframe"]="com.zoho.dpaas.dataframe"
    ["storage"]="com.zoho.dpaas.storage"
    ["util"]="com.zoho.dpaas.util"
    ["context"]="com.zoho.dpaas.context"
    ["query"]="com.zoho.dpaas.query"
    ["widgets"]="com.zoho.dpaas.widgets"
    ["udf"]="com.zoho.dpaas.udf"
    ["redis"]="com.zoho.dpaas.redis"
    ["ruleset"]="com.zoho.dpaas.ruleset"
    ["callback"]="com.zoho.dpaas.callback"
    ["common"]="com.zoho.dpaas.common"
    ["datatype"]="com.zoho.dpaas.datatype"
    ["parquet"]="com.zoho.dpaas.parquet"
    ["import"]="com.zoho.dpaas.import"
    ["executors"]="com.zoho.dpaas.executors"

    # Source-only modules (no tests yet, but can be added)
    ["parser"]="com.zoho.dpaas.parser"
    ["metrics"]="com.zoho.dpaas.metrics"
    ["sparkutil"]="com.zoho.dpaas.sparkutil"
    ["job"]="com.zoho.dpaas.job"
    ["processor"]="com.zoho.dpaas.processor"
    ["migrator"]="com.zoho.dpaas.migrator"
    ["writer"]="com.zoho.dpaas.writer"
    ["udaf"]="com.zoho.dpaas.udaf"

    # Aliases
    ["transforms"]="com.zoho.dpaas.transformer"
    ["io"]="com.zoho.dpaas.dataframe"
    ["utils"]="com.zoho.dpaas.util"
    ["all"]="com.zoho.dpaas"
)

# ─────────────────────────────────────────────────────────────────────────────
# Complete Suite Shortcuts (140 total test suites)
# ─────────────────────────────────────────────────────────────────────────────
declare -A SUITE_SHORTCUTS=(
    # ═══ TRANSFORMER SUITES (68 suites) ═══
    # Join/Union/Append operations
    ["join"]="transformer.ZDJoinSuite"
    ["union"]="transformer.ZDUnionSuite"
    ["append"]="transformer.ZDAppendSuite"
    ["merge"]="transformer.ZDMergeSuite"

    # Filter/Select/Drop
    ["filter"]="transformer.ZDFilterSuite"
    ["select"]="transformer.ZDSelectSuite"
    ["drop"]="transformer.ZDDropSuite"
    ["hide"]="transformer.ZDHideSuite"

    # Derive/Convert/SetType
    ["derive"]="transformer.ZDDeriveSuite"
    ["convert"]="transformer.ZDConvertSuite"
    ["settype"]="transformer.ZDSetTypeSuite"
    ["mlderive"]="transformer.ZDMLDeriveSuite"

    # Group/Pivot/Unpivot
    ["group"]="transformer.ZDGroupSuite"
    ["pivot"]="transformer.ZDPivotSuite"
    ["unpivot"]="transformer.ZDUnpivotSuite"
    ["bucket"]="transformer.ZDBucketSuite"

    # Split operations
    ["split"]="transformer.ZDSplitDelimiterSuite"
    ["splitdelim"]="transformer.ZDSplitDelimiterSuite"
    ["splitchar"]="transformer.ZDSplitCharSuite"
    ["splitconst"]="transformer.ZDSplitConstantSuite"
    ["splitpos"]="transformer.ZDSplitPositionSuite"
    ["splitregex"]="transformer.ZDSplitRegExSuite"

    # Combine/Flatten/Nest
    ["combine"]="transformer.ZDCombineSuite"
    ["flatten"]="transformer.ZDFlattenSuite"
    ["flattenrow"]="transformer.ZDFlattenAsRowSuite"
    ["flattencol"]="transformer.ZDFlattenAsColumnsSuite"
    ["nest"]="transformer.ZDNestSuite"
    ["unnest"]="transformer.ZDUnnestSuite"
    ["unnestenrich"]="transformer.ZDUnnestEnrichedSuite"

    # Extract operations
    ["extract"]="transformer.ZDExtractConstantSuite"
    ["extractconst"]="transformer.ZDExtractConstantSuite"
    ["extractdelim"]="transformer.ZDExtractDelimiterSuite"
    ["extractpos"]="transformer.ZDExtractPositionSuite"
    ["extractregex"]="transformer.ZDExtractRegExSuite"
    ["extractdate"]="transformer.ZDExtractDateSuite"
    ["extractemail"]="transformer.ZDExtractEmailSuite"
    ["extracturl"]="transformer.ZDExtractUrlSuite"
    ["extractquality"]="transformer.ZDExtractQualitySuite"

    # Replace operations
    ["replace"]="transformer.ZDReplaceConstantSuite"
    ["replaceconst"]="transformer.ZDReplaceConstantSuite"
    ["replacedelim"]="transformer.ZDReplaceDelimiterSuite"
    ["replacepos"]="transformer.ZDReplacePositionSuite"
    ["replaceregex"]="transformer.ZDReplaceRegExSuite"
    ["replaceconstraint"]="transformer.ZDReplaceConstraintSuite"

    # Count operations
    ["countconst"]="transformer.ZDCountConstantSuite"
    ["countdelim"]="transformer.ZDCountDelimiterSuite"
    ["countregex"]="transformer.ZDCountRegExSuite"

    # Text operations
    ["trim"]="transformer.ZDTrimSuite"
    ["case"]="transformer.ZDChangeCaseSuite"
    ["changecase"]="transformer.ZDChangeCaseSuite"
    ["truncate"]="transformer.ZDTruncateSuite"

    # Format operations
    ["date"]="transformer.ZDDateFormatSuite"
    ["dateformat"]="transformer.ZDDateFormatSuite"
    ["dateunify"]="transformer.ZDDateUnifierSuite"
    ["number"]="transformer.ZDNumberFormatSuite"
    ["numberformat"]="transformer.ZDNumberFormatSuite"
    ["duration"]="transformer.ZDDurationFormatSuite"
    ["roundoff"]="transformer.ZDRoundOffSuite"

    # Column operations
    ["rename"]="transformer.ZDRenameSuite"
    ["move"]="transformer.ZDMoveSuite"
    ["header"]="transformer.ZDHeaderSuite"
    ["duplicate"]="transformer.ZDDuplicateColumnSuite"
    ["dupcol"]="transformer.ZDDuplicateColumnSuite"
    ["internal"]="transformer.ZDInternalColumnsSuite"

    # Data quality
    ["dedup"]="transformer.ZDDeDuplicateSuite"
    ["deduplicate"]="transformer.ZDDeDuplicateSuite"
    ["deduppreview"]="transformer.ZDDeduplicatePreviewSuite"
    ["cluster"]="transformer.ZDClusterNMergeSuite"
    ["clustermerge"]="transformer.ZDClusterNMergeSuite"
    ["fill"]="transformer.ZDFillCellsSuite"
    ["fillcells"]="transformer.ZDFillCellsSuite"
    ["dataaccuracy"]="transformer.ZDDataTypeAccuracySuite"
    ["privacy"]="transformer.ZDSetPrivacySuite"
    ["setprivacy"]="transformer.ZDSetPrivacySuite"

    # Other transformer
    ["export"]="transformer.ZDExportSuite"
    ["widget"]="transformer.ZDWidgetSuite"
    ["queryrunner"]="transformer.ZDQueryRunnerSuite"
    ["sort"]="transformer.ZDSortSuite"

    # ═══ DATAFRAME IO SUITES (15 suites) ═══
    ["csv"]="dataframe.CSVReaderSuite"
    ["csvreader"]="dataframe.CSVReaderSuite"
    ["csvio"]="dataframe.ZCsvIOSuite"
    ["excel"]="dataframe.ZExcelIOSuite"
    ["excelio"]="dataframe.ZExcelIOSuite"
    ["parquetio"]="dataframe.ZParquetIOSuite"
    ["parquetread"]="dataframe.PRAQUETReaderSuite"
    ["json"]="dataframe.ZJsonIOSuite"
    ["jsonio"]="dataframe.ZJsonIOSuite"
    ["xml"]="dataframe.ZXmlIOSuite"
    ["xmlio"]="dataframe.ZXmlIOSuite"
    ["text"]="dataframe.ZTextIOSuite"
    ["textio"]="dataframe.ZTextIOSuite"
    ["html"]="dataframe.ZHtmlIOSuite"
    ["htmlio"]="dataframe.ZHtmlIOSuite"
    ["zip"]="dataframe.ZZipIOSuite"
    ["zipio"]="dataframe.ZZipIOSuite"
    ["dfio"]="dataframe.ZDataFrameIOSuite"
    ["abstractio"]="dataframe.ZAbstractDataFrameIOSuite"
    ["sparkrw"]="dataframe.ZSparkRWConstantsSuite"

    # ═══ STORAGE SUITES (8 suites) ═══
    ["dfs"]="storage.ZDDFSStorageSuite"
    ["dfsstorage"]="storage.ZDDFSStorageSuite"
    ["hdfs"]="storage.ZDHDFSStorageSuite"
    ["hdfsstorage"]="storage.ZDHDFSStorageSuite"
    ["local"]="storage.ZDLocalStorageSuite"
    ["localstorage"]="storage.ZDLocalStorageSuite"
    ["storagefactory"]="storage.ZStorageFactorySuite"
    ["fspath"]="storage.ZDFSPathParsingSuite"
    ["fspathparse"]="storage.ZFileSystemPathParsingSuite"
    ["storagepath"]="storage.ZStoragePathParsingSuite"

    # ═══ UTIL SUITES (6 suites) ═══
    ["zdutil"]="util.ZDUtilSuite"
    ["pattern"]="util.PatternTextRegExUtilSuite"
    ["patternutil"]="util.PatternTextRegExUtilSuite"
    ["relfilter"]="util.RelativeFilterUtilSuite"
    ["sqlbuilder"]="util.SparkSqlQueryBuilderSuite"
    ["ruleutil"]="util.RuleMigratorUtilSuite"
    ["joinpotential"]="util.JoinPotentialUtilSuite"

    # ═══ CONTEXT SUITES (5 suites) ═══
    ["contexttest"]="context.ContextSuite"
    ["jobcontext"]="context.JobContextSuite"
    ["rulecontext"]="context.RuleContextSuite"
    ["rulesetcontext"]="context.RuleSetContextSuite"

    # ═══ QUERY SUITES (3 suites) ═══
    ["sparkselect"]="query.SparkSelectQuerySuite"
    ["dag"]="query.DAGSuite"
    ["logical"]="query.LogicalExpressionParserSuite"

    # ═══ WIDGET SUITES (3 suites) ═══
    ["widgetgen"]="widgets.WidgetGeneratorSuite"
    ["widgetsuite"]="widgets.WidgetSuite"
    ["numwidget"]="widgets.NumericWidgetsSuite"

    # ═══ UDF SUITES (7 suites) ═══
    ["datatypeudf"]="udf.DataTypeValidationUdfsSuite"
    ["datearith"]="udf.DateArithmeticUdfsSuite"
    ["dateextract"]="udf.DateExtractionUdfsSuite"
    ["jsonudf"]="udf.JsonUdfsSuite"
    ["logicaludf"]="udf.LogicalUdfsSuite"
    ["numericudf"]="udf.NumericUdfsSuite"
    ["textconv"]="udf.TextConversionUdfsSuite"

    # ═══ CALLBACK SUITES (3 suites) ═══
    ["callback"]="callback.CallBackHandlerSuite"
    ["webcallback"]="callback.WebCallBackHandlerSuite"
    ["response"]="callback.DPAASResponseSuite"

    # ═══ COMMON SUITES (4 suites) ═══
    ["dsmodel"]="common.ZDDSModelUtilSuite"
    ["colmodel"]="common.ZDColumnModelUtilSuite"
    ["dfutil"]="common.ZDDataFrameUtilSuite"
    ["parserutil"]="common.ParserUtilSuite"

    # ═══ DATATYPE SUITES (6 suites) ═══
    ["datatypeutil"]="datatype.DataTypeUtilSuite"
    ["datematcher"]="datatype.DateMatcherSuite"
    ["durationmatch"]="datatype.DurationMatcherSuite"
    ["patternmatch"]="datatype.PatternMatcherSuite"
    ["primitive"]="datatype.PrimitiveDataTypesSuite"
    ["sqltypes"]="datatype.SqlTypesSuite"

    # ═══ PARQUET SUITES (2 suites) ═══
    ["parquetprops"]="parquet.ZParquetPropertiesSuite"
    ["parquetiosuite"]="parquet.ZDParquetIOSuite"

    # ═══ IMPORT SUITES (9 suites) ═══
    ["csvimport"]="import.CsvImportSuite"
    ["excelimport"]="import.ExcelImportSuite"
    ["jsonimport"]="import.JsonImportSuite"
    ["xmlimport"]="import.XmlImportSuite"
    ["htmlimport"]="import.HtmlImportSuite"
    ["textimport"]="import.TextImportSuite"
    ["tsvimport"]="import.TsvImportSuite"
    ["zipimport"]="import.ZipImportJobLevelSuite"

    # ═══ REDIS SUITES (1 suite) ═══
    ["redis"]="redis.RedisUtilSuite"
    ["redisutil"]="redis.RedisUtilSuite"
)

# Build ScalaTest arguments
TEST_ARGS=""
if [[ -z "$TEST_MODULES" ]]; then
    # No modules specified - run all tests
    TEST_ARGS="-w com.zoho.dpaas"
    echo -e "  ${GREEN}Running ALL tests: -w com.zoho.dpaas${NC}"
else
    # Parse comma-separated modules/suites
    IFS=',' read -ra MODULES <<< "$TEST_MODULES"
    for module in "${MODULES[@]}"; do
        module=$(echo "$module" | tr -d ' ')  # Trim whitespace
        module_lower=$(echo "$module" | tr '[:upper:]' '[:lower:]')

        # Check if it's a shortcut (now in format package.SuiteName)
        if [[ -n "${SUITE_SHORTCUTS[$module_lower]}" ]]; then
            shortcut_value="${SUITE_SHORTCUTS[$module_lower]}"
            TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.${shortcut_value}"
            echo -e "  ${BLUE}Shortcut '$module' → -s com.zoho.dpaas.${shortcut_value}${NC}"
        # Check if it's a known module
        elif [[ -n "${MODULE_MAP[$module_lower]}" ]]; then
            TEST_ARGS="$TEST_ARGS -w ${MODULE_MAP[$module_lower]}"
            echo -e "  ${BLUE}Module '$module' → -w ${MODULE_MAP[$module_lower]}${NC}"
        # Check if it's a full package name
        elif [[ "$module" == com.zoho.* ]]; then
            TEST_ARGS="$TEST_ARGS -w $module"
            echo -e "  ${BLUE}Package '$module' → -w $module${NC}"
        # Check if it looks like a Suite name (starts with uppercase, ends with Suite)
        elif [[ "$module" =~ ^[A-Z].*Suite$ ]]; then
            # Search for the suite class in test directories
            SUITE_FILE=$(find ./test/source -name "${module}.scala" -type f 2>/dev/null | head -1)
            if [[ -n "$SUITE_FILE" ]]; then
                # Extract package from file
                PACKAGE=$(grep -m1 "^package " "$SUITE_FILE" 2>/dev/null | sed 's/package //' | tr -d ' ')
                if [[ -n "$PACKAGE" ]]; then
                    TEST_ARGS="$TEST_ARGS -s ${PACKAGE}.${module}"
                    echo -e "  ${BLUE}Suite '$module' found → -s ${PACKAGE}.${module}${NC}"
                else
                    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.${module}"
                    echo -e "  ${BLUE}Suite '$module' → -s com.zoho.dpaas.${module}${NC}"
                fi
            else
                # Try common locations
                FOUND=false
                for loc in transformer dataframe util storage context query widgets udf callback common datatype parquet import redis; do
                    if [[ -f "./test/source/com/zoho/dpaas/$loc/${module}.scala" ]]; then
                        TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.${loc}.${module}"
                        echo -e "  ${BLUE}Suite '$module' in $loc → -s com.zoho.dpaas.${loc}.${module}${NC}"
                        FOUND=true
                        break
                    fi
                done
                if [[ "$FOUND" == "false" ]]; then
                    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.${module}"
                    echo -e "  ${YELLOW}Suite '$module' (guessing transformer) → -s com.zoho.dpaas.transformer.${module}${NC}"
                fi
            fi
        # Check if it starts with ZD (common prefix for zdpas suites)
        elif [[ "$module" =~ ^ZD ]]; then
            # Search for the suite file
            SUITE_FILE=$(find ./test/source -name "${module}Suite.scala" -type f 2>/dev/null | head -1)
            if [[ -n "$SUITE_FILE" ]]; then
                PACKAGE=$(grep -m1 "^package " "$SUITE_FILE" 2>/dev/null | sed 's/package //' | tr -d ' ')
                if [[ -n "$PACKAGE" ]]; then
                    TEST_ARGS="$TEST_ARGS -s ${PACKAGE}.${module}Suite"
                    echo -e "  ${BLUE}ZD pattern '$module' → -s ${PACKAGE}.${module}Suite${NC}"
                else
                    TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.${module}Suite"
                    echo -e "  ${BLUE}ZD pattern '$module' → -s com.zoho.dpaas.transformer.${module}Suite${NC}"
                fi
            else
                TEST_ARGS="$TEST_ARGS -s com.zoho.dpaas.transformer.${module}Suite"
                echo -e "  ${BLUE}ZD pattern '$module' → -s com.zoho.dpaas.transformer.${module}Suite${NC}"
            fi
        else
            # Default: treat as package suffix
            TEST_ARGS="$TEST_ARGS -w com.zoho.dpaas.${module}"
            echo -e "  ${BLUE}Pattern '$module' → -w com.zoho.dpaas.${module}${NC}"
        fi
    done
fi

echo ""
echo "━━━ Running ScalaTest (matching CI/CD) ━━━"
echo "  Full test command:"
echo "    org.scalatest.tools.Runner $TEST_ARGS -oC -u unit_tests -f test.out"
echo ""

# ════ ENVIRONMENT VERIFICATION ════
echo "  ════ ENVIRONMENT VERIFICATION ════"
echo "    DPAAS_HOME=$DPAAS_HOME"
echo "    RUNNER_DPAAS_HOME=$RUNNER_DPAAS_HOME"
echo "    BUILD_FILE_HOME=$BUILD_FILE_HOME"
if [[ "$USE_RUNNER_RESOURCES" == "true" ]]; then
    echo "    server.dir=$RUNNER_DPAAS_HOME/zdpas/spark (using GitLab runner)"
    RESOURCE_CHECK_DIR="$RUNNER_DPAAS_HOME/zdpas/spark/resources"
else
    echo "    server.dir=$DPAAS_HOME/zdpas/spark"
    RESOURCE_CHECK_DIR="$DPAAS_HOME/zdpas/spark/resources"
fi
echo ""
echo "  Critical files check (in $RESOURCE_CHECK_DIR):"
if [[ -f "$RESOURCE_CHECK_DIR/configuration.properties" ]]; then
    echo "    ✅ configuration.properties ($(stat -c %s "$RESOURCE_CHECK_DIR/configuration.properties" 2>/dev/null || stat -f %z "$RESOURCE_CHECK_DIR/configuration.properties" 2>/dev/null) bytes)"
else
    echo -e "    ${RED}❌ configuration.properties MISSING!${NC}"
fi
if [[ -f "$RESOURCE_CHECK_DIR/datastore.json" ]]; then
    echo "    ✅ datastore.json ($(stat -c %s "$RESOURCE_CHECK_DIR/datastore.json" 2>/dev/null || stat -f %z "$RESOURCE_CHECK_DIR/datastore.json" 2>/dev/null) bytes)"
else
    echo -e "    ${RED}❌ datastore.json MISSING!${NC}"
fi
if [[ -f "$RESOURCE_CHECK_DIR/datatypes.json" ]]; then
    echo "    ✅ datatypes.json ($(stat -c %s "$RESOURCE_CHECK_DIR/datatypes.json" 2>/dev/null || stat -f %z "$RESOURCE_CHECK_DIR/datatypes.json" 2>/dev/null) bytes)"
else
    echo -e "    ${YELLOW}⚠️ datatypes.json missing${NC}"
fi
echo ""

# Build classpath with resources directories
CLASSPATH="./dpaas_test.jar:./dpaas.jar"
CLASSPATH="$CLASSPATH:./resources:./test/resources"
# Use RUNNER_DPAAS resources for proper config files
if [[ "$USE_RUNNER_RESOURCES" == "true" ]]; then
    CLASSPATH="$CLASSPATH:$RUNNER_DPAAS_HOME/zdpas/spark/resources"
else
    CLASSPATH="$CLASSPATH:$DPAAS_HOME/zdpas/spark/resources"
fi
CLASSPATH="$CLASSPATH:$DPAAS_HOME/zdpas/spark/jars/*"
CLASSPATH="$CLASSPATH:$DPAAS_HOME/zdpas/spark/app_blue/ExpParser.jar"
CLASSPATH="$CLASSPATH:$DPAAS_HOME/zdpas/spark/lib/*"
# Add runner DPAAS jars as fallback
if [[ "$USE_RUNNER_RESOURCES" == "true" ]]; then
    CLASSPATH="$CLASSPATH:$RUNNER_DPAAS_HOME/zdpas/spark/jars/*"
    CLASSPATH="$CLASSPATH:$RUNNER_DPAAS_HOME/zdpas/spark/lib/*"
fi
# Add build output if exists (for CI/CD compatibility)
if [[ -d "build/ZDPAS/output/zdpas/spark/jars" ]]; then
    CLASSPATH="$CLASSPATH:build/ZDPAS/output/zdpas/spark/jars/*"
fi

echo "  Classpath includes:"
echo "    - ./dpaas_test.jar, ./dpaas.jar"
echo "    - ./resources, ./test/resources"
if [[ "$USE_RUNNER_RESOURCES" == "true" ]]; then
    echo "    - \$RUNNER_DPAAS_HOME/zdpas/spark/resources (GitLab runner)"
else
    echo "    - \$DPAAS_HOME/zdpas/spark/resources"
fi
echo "    - \$DPAAS_HOME/zdpas/spark/jars/*, lib/*"
echo ""

# Export DPAAS_HOME to ensure child process inherits it
export DPAAS_HOME
export BUILD_FILE_HOME

# Determine which DPAAS to use for server.dir (resources)
# Use GitLab runner's DPAAS for resources since it has proper config files
if [[ "$USE_RUNNER_RESOURCES" == "true" ]]; then
    TEST_SERVER_DIR="$RUNNER_DPAAS_HOME/zdpas/spark"
    TEST_RESOURCES_DIR="$RUNNER_DPAAS_HOME/zdpas/spark/resources"
    echo "  Using RUNNER_DPAAS for server.dir: $TEST_SERVER_DIR"
else
    TEST_SERVER_DIR="$DPAAS_HOME/zdpas/spark"
    TEST_RESOURCES_DIR="$DPAAS_HOME/zdpas/spark/resources"
    echo "  Using DPAAS_HOME for server.dir: $TEST_SERVER_DIR"
fi

# Run ScalaTest with the constructed arguments
# -DDPAAS_HOME must match the DPAAS used for server.dir so that
# System.getProperty("DPAAS_HOME") returns the same path as
# the actual runtime environment (separate JVM shell mode).
EFFECTIVE_DPAAS_HOME="${USE_RUNNER_RESOURCES:+$RUNNER_DPAAS_HOME}"
EFFECTIVE_DPAAS_HOME="${EFFECTIVE_DPAAS_HOME:-$DPAAS_HOME}"
java -cp "$CLASSPATH" \
    -Xmx3g \
    -DDPAAS_HOME="$EFFECTIVE_DPAAS_HOME" \
    -Dserver.dir="$TEST_SERVER_DIR" \
    -Dlog4j.configuration="file:$TEST_SERVER_DIR/conf/log4j-local.properties" \
    org.scalatest.tools.Runner \
    -R ./dpaas_test.jar \
    $TEST_ARGS \
    -oC \
    -u unit_tests \
    -f test.out 2>>err.log

echo ""
echo -e "${GREEN}✅ UNIT TESTS gate passed${NC}"

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ ALL GATES PASSED${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"
echo ""
echo "The Saturn gates compile commands match the zdpas CI/CD exactly:"
echo -e "  ${GREEN}✅ Compile: Joint Java+Scala with scalac → javac → jar${NC}"
echo -e "  ${GREEN}✅ Build Test JAR: scalac → jar with test resources${NC}"
if [[ -n "$TEST_MODULES" ]]; then
    echo -e "  ${GREEN}✅ Unit Tests: Ran tests for: $TEST_MODULES${NC}"
else
    echo -e "  ${GREEN}✅ Unit Tests: Ran ALL tests${NC}"
fi
echo ""
echo "Generated artifacts:"
echo "  - $DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar"
echo "  - ./dpaas.jar (local copy)"
echo "  - ./dpaas_test.jar"
echo "  - unit_tests/ (test reports)"
echo "  - test.out (test output)"
echo ""
echo -e "${BLUE}━━━ Usage Examples ━━━${NC}"
echo ""
echo "  # Run all tests:"
echo "  ./validate_gates.sh"
echo ""
echo "  # Run specific module tests:"
echo "  ./validate_gates.sh . transformer        # All transformer tests"
echo "  ./validate_gates.sh . dataframe          # All dataframe IO tests"
echo "  ./validate_gates.sh . util               # All utility tests"
echo ""
echo "  # Run specific test suites:"
echo "  ./validate_gates.sh . ZDJoinSuite        # Just join tests"
echo "  ./validate_gates.sh . ZDUnionSuite,ZDAppendSuite  # Multiple suites"
echo ""
echo "  # Use shortcuts:"
echo "  ./validate_gates.sh . join               # Same as ZDJoinSuite"
echo "  ./validate_gates.sh . csv                # Same as CSVReaderSuite"
echo "  ./validate_gates.sh . excel              # Same as ZExcelIOSuite"
echo ""
echo "  # Skip compilation (use existing jars):"
echo "  SKIP_COMPILE=true ./validate_gates.sh . transformer"
echo ""
echo -e "${BLUE}━━━ Available Modules (16 modules) ━━━${NC}"
echo "  transformer (68), dataframe (15), storage (8), util (6), context (5),"
echo "  query (3), widgets (3), udf (7), callback (3), common (4), datatype (6),"
echo "  parquet (2), import (9), redis (1), ruleset, executors"
echo ""
echo -e "${BLUE}━━━ Shortcut Categories (140 total suites) ━━━${NC}"
echo ""
echo "  TRANSFORMER (68 shortcuts):"
echo "    Join/Union:    join, union, append, merge"
echo "    Filter/Select: filter, select, drop, hide"
echo "    Derive:        derive, convert, settype, mlderive"
echo "    Group:         group, pivot, unpivot, bucket"
echo "    Split:         split, splitdelim, splitchar, splitconst, splitpos, splitregex"
echo "    Combine:       combine, flatten, flattenrow, flattencol, nest, unnest"
echo "    Extract:       extract, extractconst, extractdelim, extractpos, extractregex,"
echo "                   extractdate, extractemail, extracturl, extractquality"
echo "    Replace:       replace, replaceconst, replacedelim, replacepos, replaceregex"
echo "    Count:         countconst, countdelim, countregex"
echo "    Text:          trim, case, changecase, truncate"
echo "    Format:        date, dateformat, dateunify, number, numberformat, duration, roundoff"
echo "    Column:        rename, move, header, duplicate, dupcol, internal"
echo "    Quality:       dedup, deduplicate, deduppreview, cluster, clustermerge,"
echo "                   fill, fillcells, dataaccuracy, privacy, setprivacy"
echo "    Other:         export, widget, queryrunner, sort"
echo ""
echo "  DATAFRAME IO (15 shortcuts):"
echo "    csv, csvreader, csvio, excel, excelio, parquetio, parquetread,"
echo "    json, jsonio, xml, xmlio, text, textio, html, htmlio, zip, zipio"
echo ""
echo "  STORAGE (8 shortcuts):"
echo "    dfs, dfsstorage, hdfs, hdfsstorage, local, localstorage,"
echo "    storagefactory, fspath, fspathparse, storagepath"
echo ""
echo "  UTIL (6 shortcuts):"
echo "    zdutil, pattern, patternutil, relfilter, sqlbuilder, ruleutil, joinpotential"
echo ""
echo "  OTHER MODULES:"
echo "    Context (5):   contexttest, jobcontext, rulecontext, rulesetcontext"
echo "    Query (3):     sparkselect, dag, logical"
echo "    Widgets (3):   widgetgen, widgetsuite, numwidget"
echo "    UDF (7):       datatypeudf, datearith, dateextract, jsonudf, logicaludf, numericudf, textconv"
echo "    Callback (3):  callback, webcallback, response"
echo "    Common (4):    dsmodel, colmodel, dfutil, parserutil"
echo "    Datatype (6):  datatypeutil, datematcher, durationmatch, patternmatch, primitive, sqltypes"
echo "    Parquet (2):   parquetprops, parquetiosuite"
echo "    Import (9):    csvimport, excelimport, jsonimport, xmlimport, htmlimport,"
echo "                   textimport, tsvimport, zipimport"
echo "    Redis (1):     redis, redisutil"
echo ""

