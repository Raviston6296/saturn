# ZDPAS + Saturn: SonarQube Community + OWASP Dependency-Check gates

This folder is a **copy-paste template** for the **ZDPAS product repo** (worktree root), not the Saturn app repo.

## Working model (strong lint + supply chain)

| Order | Gate | Purpose |
|-------|------|--------|
| 1–3 | `compile`, `build-test-jar`, `unit-tests` | **Truth** — same as Saturn defaults (`gates/config.py` auto-discovery). |
| 4 | `sonarqube` | **Static analysis** — smells, bugs, duplication, basic security (Community limits). |
| 5 | `owasp-dependency-check` | **New/changed dependency risk** — CVEs in runtime + built jars. |

**LLM-friendly:** one `sonar-project.properties`, two small shell scripts, env vars documented below.

## 1. Install tools on the runner VM

### SonarQube Community (server)

- Self-host [SonarQube](https://www.sonarsource.com/products/sonarqube/downloads/) or use your org’s instance.
- Create a project and a **token** for CI/analysis.

### SonarScanner (CLI)

- [Install](https://docs.sonarsource.com/sonarqube/latest/analyzing-source-code/scanners/sonarscanner/) `sonar-scanner` on the runner **or** set `SONAR_SCANNER_HOME` to the unzip path.

### OWASP Dependency-Check

- Download [OWASP Dependency-Check](https://github.com/jeremylong/DependencyCheck) CLI distribution.
- Set `OWASP_DEPENDENCY_CHECK_HOME` to the extracted folder (contains `bin/dependency-check.sh`).

**NVD API (recommended):** register for an [NVD API key](https://nvd.nist.gov/developers/request-an-api-key) and set `NVD_API_KEY` to avoid rate limits / failures.

## 2. Copy into ZDPAS repo

```text
your-zdpas-repo/
├── .saturn/
│   ├── gates.yaml              # merge: see gates-sonar-owasp.addon.yaml
│   └── scripts/
│       ├── sonarqube-gate.sh
│       └── owasp-dependency-check-gate.sh
├── sonar-project.properties    # copy from sonar-project.properties.example, edit keys
```

Merge **addon** gates **after** your compile / test gates (or paste the three standard ZDPAS commands from Saturn `gates/config.py` if you are not using auto-discovery).

## 3. Environment variables (saturn.env / GitLab CI)

| Variable | Required | Purpose |
|----------|----------|---------|
| `SONAR_HOST_URL` | Yes (SonarQube) | e.g. `https://sonar.example.com` |
| `SONAR_TOKEN` | Yes | Project analysis token |
| `SONAR_SCANNER_HOME` | If `sonar-scanner` not on PATH | Scanner install dir |
| `OWASP_DEPENDENCY_CHECK_HOME` | Yes | OWASP CLI root |
| `NVD_API_KEY` | Strongly recommended | NVD API access |
| `OWASP_FAIL_ON_CVSS` | No (default `7`) | Fail gate if max CVSS ≥ this |
| `DPAAS_HOME` | Yes | Already used by ZDPAS gates |

## 4. Gate behaviour

- **Sonar:** fails if Quality Gate fails (configure in Sonar) or scanner exits non‑zero.
- **OWASP:** scans `dpaas.jar`, `dpaas_test.jar`, and `${DPAAS_HOME}/zdpas/spark/jars` so **new jars** pulled into the runtime tree are included. Adjust paths in `owasp-dependency-check-gate.sh` if your layout differs.

## 5. When not to run in Saturn

Full Sonar + OWASP can take **several minutes** and hit the network (NVD). For **very fast** agent loops, run **compile + unit-tests** in Saturn and run **Sonar + OWASP only in GitLab CI** on MR. Keep the same scripts in CI for parity.
