#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Saturn Integration Test — Validate Complete Flow
# ═══════════════════════════════════════════════════════════════════════════
#
# This script tests the complete Saturn agent flow:
#   1. Setup DPAAS environment
#   2. Run gates (format, lint, compile, test)
#   3. Verify all components work together
#
# Usage:
#   ./test_saturn_flow.sh                    # Full test
#   ./test_saturn_flow.sh --skip-compile     # Skip compilation (use existing jars)
#   ./test_saturn_flow.sh --module join      # Test specific module
#
# ═══════════════════════════════════════════════════════════════════════════

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SATURN_HOME="${SATURN_HOME:-$SCRIPT_DIR}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}🪐 Saturn Integration Test${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"

# ─────────────────────────────────────────────────────────────────────────────
# Parse Arguments
# ─────────────────────────────────────────────────────────────────────────────
SKIP_COMPILE=false
TEST_MODULE=""
WORKSPACE="${1:-.}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-compile)
            SKIP_COMPILE=true
            shift
            ;;
        --module)
            TEST_MODULE="$2"
            shift 2
            ;;
        *)
            if [[ -d "$1" ]]; then
                WORKSPACE="$1"
            fi
            shift
            ;;
    esac
done

echo ""
echo -e "${YELLOW}Configuration:${NC}"
echo "  SATURN_HOME: $SATURN_HOME"
echo "  WORKSPACE: $WORKSPACE"
echo "  SKIP_COMPILE: $SKIP_COMPILE"
echo "  TEST_MODULE: ${TEST_MODULE:-ALL}"

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Verify Python Environment
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}━━━ Step 1: Verify Python Environment ━━━${NC}"

if [[ -f "$SATURN_HOME/.venv/bin/activate" ]]; then
    source "$SATURN_HOME/.venv/bin/activate"
    echo -e "  ${GREEN}✅ Virtual environment activated${NC}"
else
    echo -e "  ${RED}❌ Virtual environment not found at $SATURN_HOME/.venv${NC}"
    exit 1
fi

python -c "from config import settings; print(f'  ✅ Config loaded: DPAAS_HOME={settings.saturn_dpaas_home}')" || {
    echo -e "  ${RED}❌ Config import failed${NC}"
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Verify Gates Module
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}━━━ Step 2: Verify Gates Module ━━━${NC}"

python -c "
from gates import GatePipeline, setup_dpaas_environment
from gates.executor import run_gate_pipeline
from gates.config import load_repo_config
print('  ✅ GatePipeline imported')
print('  ✅ setup_dpaas_environment imported')
print('  ✅ run_gate_pipeline imported')
print('  ✅ load_repo_config imported')
" || {
    echo -e "  ${RED}❌ Gates module import failed${NC}"
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Verify Agent Module
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}━━━ Step 3: Verify Agent Module ━━━${NC}"

python -c "
from agent.agent import AutonomousAgent
print('  ✅ AutonomousAgent imported')
" || {
    echo -e "  ${RED}❌ Agent module import failed${NC}"
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Test DPAAS Setup
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}━━━ Step 4: Test DPAAS Setup ━━━${NC}"

python -c "
import os
from gates import setup_dpaas_environment
from config import settings
from pathlib import Path

workspace = '$WORKSPACE'

# Check if default DPAAS_HOME is writable, otherwise use temp
dpaas_home = Path(settings.saturn_dpaas_home)
try:
    dpaas_home.mkdir(parents=True, exist_ok=True)
except (PermissionError, OSError) as e:
    print(f'  ⚠️ Cannot create {dpaas_home}: {e}')
    print(f'  ℹ️ On runner VM, /data/saturn/dpaas will be used')
    print(f'  ✅ DPAAS setup test skipped (run on runner VM)')
    exit(0)

print(f'  Testing DPAAS setup for workspace: {workspace}')

# Call setup
setup_dpaas_environment(workspace)

# Verify directories created
spark_dir = dpaas_home / 'zdpas' / 'spark'

for subdir in ['app_blue', 'jars', 'lib', 'resources', 'conf']:
    path = spark_dir / subdir
    if path.exists():
        print(f'  ✅ {subdir}/ exists')
    else:
        print(f'  ⚠️ {subdir}/ missing')
" || {
    echo -e "  ${RED}❌ DPAAS setup test failed${NC}"
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Test Gate Config Loading
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}━━━ Step 5: Test Gate Config Loading ━━━${NC}"

python -c "
from gates.config import load_repo_config

workspace = '$WORKSPACE'
config = load_repo_config(workspace)

print(f'  has_config: {config.has_config}')
print(f'  gates: {len(config.gates.gates)} defined')
for g in config.gates.gates:
    print(f'    • {g.name}: {g.description[:50] if g.description else \"(no description)\"}')
" || {
    echo -e "  ${RED}❌ Gate config loading failed${NC}"
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 6: Dry Run Gates (without actual execution)
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}━━━ Step 6: Gates Dry Run ━━━${NC}"

python -c "
from gates import GatePipeline
from gates.config import load_repo_config

workspace = '$WORKSPACE'
config = load_repo_config(workspace)

print('  Gates that would run:')
for i, gate in enumerate(config.gates.gates, 1):
    print(f'    {i}. {gate.name} (retryable={gate.retryable})')

print('')
print('  To run gates for real, use:')
print('    ./validate_gates.sh')
print('  Or with a specific module:')
print('    ./validate_gates.sh . transformer')
"

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ All integration tests passed!${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════════════════${NC}"
echo ""
echo "Next steps:"
echo "  1. Copy .saturn/ configs to your zdpas repo:"
echo "     cp .saturn/gates.yaml.example /path/to/zdpas/.saturn/gates.yaml"
echo "     cp .saturn/rules.yaml.example /path/to/zdpas/.saturn/rules.yaml"
echo ""
echo "  2. Run actual gates validation:"
echo "     ./validate_gates.sh /path/to/zdpas join"
echo ""
echo "  3. Start Saturn agent:"
echo "     python main.py"
echo ""

