#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Saturn DPAAS Setup — Isolated Environment (No Conflict with GitLab Runner)
# ═══════════════════════════════════════════════════════════════════════════
#
# This script sets up Saturn's OWN DPAAS_HOME, completely separate from
# the GitLab runner's DPAAS_HOME, to avoid conflicts.
#
# Usage:
#   ./setup_saturn_dpaas.sh                    # Setup from CI/CD cache
#   ./setup_saturn_dpaas.sh --from-runner      # Copy from GitLab runner's DPAAS_HOME
#   ./setup_saturn_dpaas.sh --from-url <url>   # Download from URL
#
# Directory Structure Created:
#   /data/saturn/dpaas/zdpas/spark/
#   ├── app_blue/     # Saturn's compiled jars (dpaas.jar)
#   ├── jars/         # Dependencies from dpaas.tar.gz
#   ├── lib/          # Libraries
#   ├── resources/    # Config files (datastore.json, etc.)
#   └── conf/         # log4j configs
#
# ═══════════════════════════════════════════════════════════════════════════

set -e

# ── Configuration ──
# All paths MUST be set in the caller's environment (shell profile or CI/CD variables).
# Hard-coded defaults are intentionally not provided — every deployment differs.
# Typical runner VM values (set in ~/.bashrc or /etc/environment, NOT here):
#   export SATURN_HOME=/home/gitlab-runner/saturn
#   export SATURN_DPAAS_HOME=/data/saturn/dpaas      # Saturn's isolated DPAAS dir
#   export GITLAB_RUNNER_DPAAS_HOME=/opt/dpaas       # The runner's own DPAAS dir
#   export BUILD_FILE_HOME=/home/gitlab-runner/build-files
SATURN_HOME="${SATURN_HOME:?SATURN_HOME must be set in the environment}"
SATURN_DPAAS_HOME="${SATURN_DPAAS_HOME:?SATURN_DPAAS_HOME must be set in the environment}"
GITLAB_RUNNER_DPAAS_HOME="${GITLAB_RUNNER_DPAAS_HOME:?GITLAB_RUNNER_DPAAS_HOME must be set in the environment}"
BUILD_FILE_HOME="${BUILD_FILE_HOME:?BUILD_FILE_HOME must be set in the environment}"

# CI/CD cache location (where GitLab stores build artifacts)
CICD_CACHE_DIR="${CICD_CACHE_DIR:-/home/gitlab-runner/builds}"

echo "═══════════════════════════════════════════════════════════════════════════"
echo "🪐 Saturn DPAAS Setup — Isolated Environment"
echo "═══════════════════════════════════════════════════════════════════════════"
echo "  SATURN_DPAAS_HOME:       $SATURN_DPAAS_HOME"
echo "  GITLAB_RUNNER_DPAAS_HOME: $GITLAB_RUNNER_DPAAS_HOME"
echo "  BUILD_FILE_HOME:         $BUILD_FILE_HOME"
echo "═══════════════════════════════════════════════════════════════════════════"

# ── Parse Arguments ──
SOURCE="cache"
URL=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --from-runner)
            SOURCE="runner"
            shift
            ;;
        --from-url)
            SOURCE="url"
            URL="$2"
            shift 2
            ;;
        --from-cache)
            SOURCE="cache"
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ── Create Saturn's DPAAS directory structure ──
echo ""
echo "━━━ Creating Saturn DPAAS directory structure ━━━"
mkdir -p "$SATURN_DPAAS_HOME/zdpas/spark/app_blue"
mkdir -p "$SATURN_DPAAS_HOME/zdpas/spark/jars"
mkdir -p "$SATURN_DPAAS_HOME/zdpas/spark/lib"
mkdir -p "$SATURN_DPAAS_HOME/zdpas/spark/resources"
mkdir -p "$SATURN_DPAAS_HOME/zdpas/spark/conf"
echo "  ✅ Directories created"

# ── Function: Find latest dpaas.tar.gz in CI/CD cache ──
find_cicd_cache() {
    echo "  🔍 Searching for dpaas.tar.gz in CI/CD cache..."

    # Search common locations
    SEARCH_PATHS=(
        "$CICD_CACHE_DIR"
        "/home/gitlab-runner/cache"
        "/home/gitlab-runner/builds/*/*/build/ZDPAS/output"
        "$SATURN_HOME/worktrees/*/build/ZDPAS/output"
    )

    for path in "${SEARCH_PATHS[@]}"; do
        if [[ -d "$(dirname "$path")" ]]; then
            FOUND=$(find $path -name "dpaas.tar.gz" -type f 2>/dev/null | head -1)
            if [[ -n "$FOUND" ]]; then
                echo "  ✅ Found: $FOUND"
                echo "$FOUND"
                return 0
            fi
        fi
    done

    echo "  ⚠️ No dpaas.tar.gz found in cache"
    return 1
}

# ── Function: Find latest dpaas_test.tar.gz in CI/CD cache ──
find_cicd_test_cache() {
    echo "  🔍 Searching for dpaas_test.tar.gz in CI/CD cache..."

    SEARCH_PATHS=(
        "$CICD_CACHE_DIR"
        "/home/gitlab-runner/cache"
        "/home/gitlab-runner/builds/*/*/build/ZDPAS/output"
        "$SATURN_HOME/worktrees/*/build/ZDPAS/output"
    )

    for path in "${SEARCH_PATHS[@]}"; do
        if [[ -d "$(dirname "$path")" ]]; then
            FOUND=$(find $path -name "dpaas_test.tar.gz" -type f 2>/dev/null | head -1)
            if [[ -n "$FOUND" ]]; then
                echo "  ✅ Found: $FOUND"
                echo "$FOUND"
                return 0
            fi
        fi
    done

    echo "  ⚠️ No dpaas_test.tar.gz found in cache"
    return 1
}

# ── Setup based on source ──
case "$SOURCE" in
    cache)
        echo ""
        echo "━━━ Setting up from CI/CD cache ━━━"

        # Find and extract dpaas.tar.gz
        DPAAS_TAR=$(find_cicd_cache)
        if [[ -n "$DPAAS_TAR" ]] && [[ -f "$DPAAS_TAR" ]]; then
            echo "  📦 Extracting dpaas.tar.gz..."
            tar -xzf "$DPAAS_TAR" -C "$SATURN_DPAAS_HOME"
            echo "  ✅ Extracted to $SATURN_DPAAS_HOME"
        else
            echo "  ⚠️ dpaas.tar.gz not found — will compile from source"
        fi

        # Find and extract dpaas_test.tar.gz
        DPAAS_TEST_TAR=$(find_cicd_test_cache)
        if [[ -n "$DPAAS_TEST_TAR" ]] && [[ -f "$DPAAS_TEST_TAR" ]]; then
            echo "  📦 Extracting dpaas_test.tar.gz..."
            tar -xzf "$DPAAS_TEST_TAR" -C "$SATURN_DPAAS_HOME"
            echo "  ✅ Extracted test resources"
        fi
        ;;

    runner)
        echo ""
        echo "━━━ Copying from GitLab runner's DPAAS_HOME ━━━"

        if [[ ! -d "$GITLAB_RUNNER_DPAAS_HOME/zdpas" ]]; then
            echo "  ❌ GitLab runner DPAAS_HOME not found: $GITLAB_RUNNER_DPAAS_HOME"
            exit 1
        fi

        # Copy jars and libs (not app_blue — Saturn compiles its own)
        echo "  📋 Copying jars..."
        cp -r "$GITLAB_RUNNER_DPAAS_HOME/zdpas/spark/jars/"* "$SATURN_DPAAS_HOME/zdpas/spark/jars/" 2>/dev/null || true

        echo "  📋 Copying lib..."
        cp -r "$GITLAB_RUNNER_DPAAS_HOME/zdpas/spark/lib/"* "$SATURN_DPAAS_HOME/zdpas/spark/lib/" 2>/dev/null || true

        echo "  📋 Copying resources..."
        cp -r "$GITLAB_RUNNER_DPAAS_HOME/zdpas/spark/resources/"* "$SATURN_DPAAS_HOME/zdpas/spark/resources/" 2>/dev/null || true

        echo "  📋 Copying conf..."
        cp -r "$GITLAB_RUNNER_DPAAS_HOME/zdpas/spark/conf/"* "$SATURN_DPAAS_HOME/zdpas/spark/conf/" 2>/dev/null || true

        # Copy ExpParser.jar to app_blue
        if [[ -f "$GITLAB_RUNNER_DPAAS_HOME/zdpas/spark/app_blue/ExpParser.jar" ]]; then
            cp "$GITLAB_RUNNER_DPAAS_HOME/zdpas/spark/app_blue/ExpParser.jar" "$SATURN_DPAAS_HOME/zdpas/spark/app_blue/"
        fi

        echo "  ✅ Copied from GitLab runner"
        ;;

    url)
        echo ""
        echo "━━━ Downloading from URL ━━━"

        if [[ -z "$URL" ]]; then
            echo "  ❌ No URL provided"
            exit 1
        fi

        echo "  📥 Downloading: $URL"
        curl -fsSL "$URL" -o /tmp/dpaas.tar.gz
        tar -xzf /tmp/dpaas.tar.gz -C "$SATURN_DPAAS_HOME"
        rm /tmp/dpaas.tar.gz
        echo "  ✅ Downloaded and extracted"
        ;;
esac

# ── Copy datastore.json if available ──
echo ""
echo "━━━ Setting up configuration files ━━━"
if [[ -f "$BUILD_FILE_HOME/datastore.json" ]]; then
    cp "$BUILD_FILE_HOME/datastore.json" "$SATURN_DPAAS_HOME/zdpas/spark/resources/"
    echo "  ✅ Copied datastore.json"
fi

# ── Create log4j config if not exists ──
if [[ ! -f "$SATURN_DPAAS_HOME/zdpas/spark/conf/log4j-local.properties" ]]; then
    cat > "$SATURN_DPAAS_HOME/zdpas/spark/conf/log4j-local.properties" << 'EOF'
log4j.rootLogger=WARN, console
log4j.appender.console=org.apache.log4j.ConsoleAppender
log4j.appender.console.layout=org.apache.log4j.PatternLayout
log4j.appender.console.layout.ConversionPattern=%d{yyyy-MM-dd HH:mm:ss} %-5p %c{1}:%L - %m%n
EOF
    echo "  ✅ Created log4j-local.properties"
fi

# ── Create saturn_env.sh for sourcing ──
echo ""
echo "━━━ Creating saturn_env.sh ━━━"
cat > "$SATURN_HOME/saturn_env.sh" << EOF
#!/bin/bash
# Saturn Environment Variables
# Source this before running Saturn or validate_gates.sh

export SATURN_HOME="$SATURN_HOME"
export DPAAS_HOME="$SATURN_DPAAS_HOME"
export BUILD_FILE_HOME="$BUILD_FILE_HOME"

# Override PATH to use Saturn's environment
export PATH="\$SATURN_HOME/.venv/bin:\$PATH"

echo "🪐 Saturn environment loaded"
echo "  DPAAS_HOME=\$DPAAS_HOME"
echo "  BUILD_FILE_HOME=\$BUILD_FILE_HOME"
EOF
chmod +x "$SATURN_HOME/saturn_env.sh"
echo "  ✅ Created saturn_env.sh"

# ── Summary ──
echo ""
echo "═══════════════════════════════════════════════════════════════════════════"
echo "✅ Saturn DPAAS Setup Complete"
echo "═══════════════════════════════════════════════════════════════════════════"
echo ""
echo "Directory structure:"
ls -la "$SATURN_DPAAS_HOME/zdpas/spark/" 2>/dev/null || echo "  (empty — will be populated on first compile)"
echo ""
echo "To use Saturn's isolated environment:"
echo "  source $SATURN_HOME/saturn_env.sh"
echo ""
echo "To run gates with Saturn's DPAAS_HOME:"
echo "  source $SATURN_HOME/saturn_env.sh"
echo "  cd /path/to/zdpas/worktree"
echo "  $SATURN_HOME/validate_gates.sh . join"
echo ""

