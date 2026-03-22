#!/usr/bin/env bash
# Saturn gate: SonarQube Community analysis via sonar-scanner.
# Prereq: sonar-project.properties in workspace root; server + token configured.
set -euo pipefail

echo "━━━ Gate: SonarQube (sonar-scanner) ━━━"

if [[ ! -f "sonar-project.properties" ]]; then
  echo "❌ sonar-project.properties not found in workspace root."
  echo "   Copy examples/zdpas-security-gates/sonar-project.properties.example and edit."
  exit 1
fi

: "${SONAR_HOST_URL:?Set SONAR_HOST_URL (e.g. https://your-sonar.example.com)}"
: "${SONAR_TOKEN:?Set SONAR_TOKEN}"

SCANNER="sonar-scanner"
if ! command -v sonar-scanner &>/dev/null; then
  if [[ -n "${SONAR_SCANNER_HOME:-}" && -x "${SONAR_SCANNER_HOME}/bin/sonar-scanner" ]]; then
    SCANNER="${SONAR_SCANNER_HOME}/bin/sonar-scanner"
  else
    echo "❌ sonar-scanner not found. Install it or set SONAR_SCANNER_HOME."
    exit 1
  fi
fi

# Optional: re-use compiled_classes if your compile gate leaves it; else Sonar may warn.
export SONAR_SCANNER_OPTS="${SONAR_SCANNER_OPTS:-}"

exec "$SCANNER" \
  -Dsonar.host.url="$SONAR_HOST_URL" \
  -Dsonar.login="$SONAR_TOKEN"
