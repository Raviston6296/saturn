#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Saturn Gates Test — Run Single Suite (ZDTrimSuite)
# ═══════════════════════════════════════════════════════════════════════════
#
# Simple test to verify gates are working correctly.
# Run this on the Saturn runner VM after setup.
#
# Usage:
#   ./test_single_suite.sh                    # Test with ZDTrimSuite
#   ./test_single_suite.sh ZDJoinSuite        # Test with specific suite
#   ./test_single_suite.sh transformer        # Test entire module
#
# ═══════════════════════════════════════════════════════════════════════════

set -e

SUITE="${1:-trim}"  # Default to trim (ZDTrimSuite)

echo "═══════════════════════════════════════════════════════════════════════════"
echo "🪐 Saturn Gates Test — Single Suite"
echo "═══════════════════════════════════════════════════════════════════════════"
echo "  Suite: $SUITE"
echo "  DPAAS_HOME: ${DPAAS_HOME:-NOT SET}"
echo "  BUILD_FILE_HOME: ${BUILD_FILE_HOME:-NOT SET}"
echo "═══════════════════════════════════════════════════════════════════════════"

# Check environment
if [[ -z "$DPAAS_HOME" ]]; then
    echo "❌ DPAAS_HOME not set. Run: export DPAAS_HOME=/data/saturn/dpaas"
    exit 1
fi

# Check we're in zdpas repo
if [[ ! -d "./source/com/zoho/dpaas" ]]; then
    echo "❌ Not in zdpas repository. cd to your zdpas worktree first."
    exit 1
fi

echo ""
echo "━━━ Setting SATURN_TEST_MODULES=$SUITE ━━━"
export SATURN_TEST_MODULES="$SUITE"

echo ""
echo "━━━ Testing Gate Detection ━━━"
cd "$(dirname "$0")"
source .venv/bin/activate

python -c "
from gates.config import _detect_gates
from pathlib import Path
import os

workspace = os.environ.get('ZDPAS_WORKTREE', '.')
gates, project_type = _detect_gates(Path(workspace))
print(f'  Project type: {project_type}')
print(f'  Gates detected: {len(gates)}')
for g in gates:
    print(f'    • {g.name}: {g.description}')
"

echo ""
echo "━━━ Testing Module Detection ━━━"
python -c "
from gates.incremental import get_affected_modules_zdpas

# Simulate a change to transformer
changed_files = ['source/com/zoho/dpaas/transformer/ZDTrim.scala']
modules = get_affected_modules_zdpas(changed_files)
print(f'  Changed files: {changed_files}')
print(f'  Detected modules: {modules}')
"

echo ""
echo "━━━ Testing Suite Shortcut Resolution ━━━"
python -c "
suite = '$SUITE'
print(f'  Input: {suite}')

# Simulate the case statement logic
suite_lower = suite.lower()
shortcuts = {
    'trim': 'com.zoho.dpaas.transformer.ZDTrimSuite',
    'join': 'com.zoho.dpaas.transformer.ZDJoinSuite',
    'append': 'com.zoho.dpaas.transformer.ZDAppendSuite',
    'transformer': 'com.zoho.dpaas.transformer (package)',
}
if suite_lower in shortcuts:
    print(f'  Resolved: {shortcuts[suite_lower]}')
else:
    print(f'  Resolved: com.zoho.dpaas.{suite} (default)')
"

echo ""
echo "═══════════════════════════════════════════════════════════════════════════"
echo "✅ Gates configuration verified!"
echo "═══════════════════════════════════════════════════════════════════════════"
echo ""
echo "To run actual gates on zdpas worktree:"
echo ""
echo "  # Option 1: Using validate_gates.sh"
echo "  cd /path/to/zdpas/worktree"
echo "  export DPAAS_HOME=/data/saturn/dpaas"
echo "  export BUILD_FILE_HOME=/home/gitlab-runner/build-files"
echo "  /home/gitlab-runner/saturn/validate_gates.sh . $SUITE"
echo ""
echo "  # Option 2: Submit task to Saturn agent"
echo "  curl -X POST http://localhost:8000/tasks/submit \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"description\": \"Run ZDTrimSuite tests\", \"task_type\": \"test\"}'"
echo ""
echo "  # Option 3: Direct test command (skip LLM, just run gates)"
echo "  curl -X POST http://localhost:8000/tasks/submit \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"description\": \"GATE_TEST: trim\", \"task_type\": \"test\"}'"
echo ""

