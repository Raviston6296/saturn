#!/usr/bin/env bash
# Saturn gate: OWASP Dependency-Check — CVE scan on built jars + runtime classpath jars.
# Catches risky versions when new jars appear under DPAAS_HOME or in dpaas.jar.
set -euo pipefail

echo "━━━ Gate: OWASP Dependency-Check ━━━"

: "${OWASP_DEPENDENCY_CHECK_HOME:?Set OWASP_DEPENDENCY_CHECK_HOME to CLI install path}"
: "${DPAAS_HOME:?Set DPAAS_HOME}"

DC="${OWASP_DEPENDENCY_CHECK_HOME}/bin/dependency-check.sh"
if [[ ! -x "$DC" ]]; then
  echo "❌ Not found or not executable: $DC"
  exit 1
fi

FAIL_ON_CVSS="${OWASP_FAIL_ON_CVSS:-7}"
OUTDIR="${OWASP_REPORT_DIR:-./.owasp-dependency-check}"
mkdir -p "$OUTDIR"

test -f dpaas.jar || { echo "❌ dpaas.jar missing — run compile gate first"; exit 1; }

SCANS=( )
[[ -f dpaas.jar ]] && SCANS+=( --scan "./dpaas.jar" )
[[ -f dpaas_test.jar ]] && SCANS+=( --scan "./dpaas_test.jar" )
[[ -d "${DPAAS_HOME}/zdpas/spark/jars" ]] && SCANS+=( --scan "${DPAAS_HOME}/zdpas/spark/jars" )

if [[ ${#SCANS[@]} -eq 0 ]]; then
  echo "❌ No scan paths resolved."
  exit 1
fi

NVD_ARGS=( )
if [[ -n "${NVD_API_KEY:-}" ]]; then
  NVD_ARGS+=( --nvdApiKey "$NVD_API_KEY" )
fi

echo "  Scanning: dpaas.jar, dpaas_test.jar (if present), DPAAS jars dir"
echo "  failOnCVSS >= ${FAIL_ON_CVSS}  (set OWASP_FAIL_ON_CVSS to change)"

exec "$DC" \
  --project "zdpas-saturn" \
  --format JSON \
  --format HTML \
  --out "$OUTDIR" \
  --failOnCVSS "$FAIL_ON_CVSS" \
  "${NVD_ARGS[@]}" \
  "${SCANS[@]}"
