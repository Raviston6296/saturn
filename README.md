# 🪐 Saturn — Autonomous Coding Agent

## Architecture Overview

Saturn is an autonomous coding agent that receives tasks in plain English,
writes code, validates it through deterministic gates, and creates Merge Requests —
all without human intervention.

```
                         Zoho Cliq / REST API
                                  │
                                  ▼
                     ┌──────────────────────────┐
                     │   Saturn Server           │
                     │   (FastAPI + AsyncIO)     │
                     │                           │
                     │   POST /webhook/cliq      │
                     │   POST /tasks/submit      │
                     │   GET  /health            │
                     └──────────┬────────────────┘
                                │
                                ▼
                     ┌──────────────────────────┐
                     │   Async Task Queue        │
                     │   (in-memory asyncio.Queue)│
                     └──────────┬────────────────┘
                                │
                                ▼
                     ┌──────────────────────────┐
                     │   Background Worker       │
                     │   (TaskWorker — async)    │
                     │                           │
                     │   Picks tasks from queue  │
                     │   One task at a time      │
                     └──────────┬────────────────┘
                                │
                                ▼
                     ┌──────────────────────────┐
                     │   Job Execution           │
                     │                           │
                     │   1. worktree create      │
                     │   2. Cursor CLI / LLM     │
                     │   3. gates pipeline       │
                     │   4. commit → push → MR   │
                     │   5. worktree remove      │
                     └──────────────────────────┘
                                │
                                ▼
                            GitLab MR
```

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [Core Components](#2-core-components)
3. [Deterministic Gates](#3-deterministic-gates)
4. [Incremental Validation](#4-incremental-validation)
5. [Gate Retry Loop (Self-Healing)](#5-gate-retry-loop-self-healing)
6. [Repository Configuration (.saturn/)](#6-repository-configuration-saturn)
7. [Dev Environment Setup](#7-dev-environment-setup)
8. [Running Saturn Locally](#8-running-saturn-locally)
9. [Deployment (GitLab Runner)](#9-deployment-gitlab-runner)
10. [CI Pipeline Integration](#10-ci-pipeline-integration)
11. [API Reference](#11-api-reference)
12. [Configuration Reference](#12-configuration-reference)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Project Structure

```
saturn/
│
├── server/
│   ├── __init__.py
│   ├── app.py                      # FastAPI app factory + lifespan (starts worker)
│   ├── models.py                   # Pydantic models (TaskRequest, TaskType, TaskPriority)
│   └── routes/
│       ├── __init__.py
│       ├── cliq_webhook.py         # POST /webhook/cliq (Zoho Cliq bot)
│       ├── health.py               # GET /health
│       └── tasks.py                # POST /tasks/submit (direct REST API)
│
├── agent/
│   ├── __init__.py
│   ├── agent.py                    # AutonomousAgent — main orchestrator
│   ├── brain.py                    # Legacy LLM: Ollama (local) / Anthropic (cloud)
│   ├── context.py                  # Repo + worktree snapshot builder
│   ├── cursor_cli.py               # Cursor CLI wrapper (primary coding engine)
│   ├── memory.py                   # Two-tier: repo memory + task log
│   └── prompts.py                  # System prompt + hard-problem addon
│
├── gates/
│   ├── __init__.py                 # GatePipeline (orchestrator)
│   ├── config.py                   # Config loader + auto-discovery
│   ├── executor.py                 # Gate executor with retry loop
│   ├── incremental.py              # Module mapping + test targeting
│   └── risk.py                     # Patch risk checker
│
├── dispatcher/
│   ├── __init__.py
│   ├── queue.py                    # Async task queue (asyncio.Queue)
│   ├── worker.py                   # Background TaskWorker (processes queue)
│   └── workspace.py                # RepoManager (bare clone + worktrees)
│
├── tools/
│   ├── __init__.py
│   ├── filesystem.py               # read_file, edit_file, create_file, list_directory
│   ├── git.py                      # status, diff, log, commit, push
│   ├── gitlab.py                   # create_merge_request via GitLab API
│   ├── registry.py                 # Tool schemas + executor router
│   ├── search.py                   # Code search (grep/ripgrep)
│   └── terminal.py                 # run_command (sandboxed shell execution)
│
├── integrations/
│   ├── __init__.py
│   ├── cliq.py                     # Zoho Cliq bot messaging (ZAPI key)
│   └── deluge_bot_script.deluge    # Deluge script for Cliq bot
│
├── repo_indexer/
│   ├── __init__.py
│   ├── config.py                   # Indexer configuration
│   ├── indexer.py                  # Code indexing (ChromaDB embeddings)
│   ├── llm.py                      # LLM integration for indexing
│   ├── main.py                     # CLI entry point (typer)
│   ├── retriever.py                # Semantic code retrieval
│   └── watcher.py                  # File system watcher (watchdog)
│
├── utils/
│   └── logging.py                  # Logging utilities
│
├── tests/
│   ├── __init__.py
│   ├── test_agent.py
│   ├── test_terminal.py
│   ├── test_tools.py
│   └── test_webhook.py
│
├── main.py                         # Entry point (uvicorn server)
├── config.py                       # Settings from saturn.env (Pydantic)
├── pyproject.toml                  # Dependencies + scripts
├── test_server.py                  # API endpoint tests
├── saturn.env                      # Your environment variables (git-ignored)
└── saturn.env.example              # Environment template
```

---

## 2. Core Components

### 2.1 Saturn Server (`server/app.py`)

FastAPI server with async task queue. Receives tasks from Cliq webhooks or direct API calls.
Uses lifespan events to start/stop the background worker.

```
POST /webhook/cliq   → receive task from Zoho Cliq bot
POST /tasks/submit   → submit task via REST API
GET  /health         → health check + version info
```

### 2.2 Task Worker (`dispatcher/worker.py`)

Background async worker that:
- Pulls tasks from the in-memory asyncio.Queue
- Creates git worktrees for isolation
- Invokes the AutonomousAgent
- Reports results back to Cliq

### 2.3 RepoManager (`dispatcher/workspace.py`)

Manages a persistent bare clone. Creates lightweight git worktrees for each task.
Maintains a shared build cache across worktrees.

```
/data/saturn/
├── repo/                        # bare clone (persistent)
├── tasks/
│   ├── task-a1b2c3/            # worktree for task A
│   ├── task-d4e5f6/            # worktree for task B
│   └── .build_cache/           # shared build artifacts (optional)
```

### 2.4 Agent (`agent/agent.py`)

The AutonomousAgent orchestrates task execution:

```
agent.run("Fix date parsing for ISO-8601")
  │
  ├── Cursor CLI (default) or LLM edits code
  ├── Gate pipeline (risk → compile → test)
  │    ├── pass → commit → push → MR
  │    └── fail → agent fixes → retry gates
  └── Report summary
```

### 2.5 Coding Engine

**Primary: Cursor CLI** (`LLM_PROVIDER=cursor`)
- All coding work delegated to Cursor Agent CLI (`agent` binary)
- Install: `curl https://cursor.com/install -fsS | bash`
- Flags: `--print --trust --yolo` for non-interactive mode

**Legacy: Direct LLM** (`LLM_PROVIDER=ollama` or `anthropic`)
- Ollama (local): `qwen2.5:7b` — free, fast
- Anthropic (cloud): `claude-sonnet-4-20250514` — powerful

---

## 3. Deterministic Gates

Gates are ordered validation steps that run after the agent edits code.
Defined in `.saturn/gates.yaml` in the target repository.

### Gate Execution Order

```
setup          (extract cached build into DPAAS_HOME)
    │
    ▼
compile        (scalac + javac joint compilation)
    │
    ▼
compile-tests  (compile test sources)
    │
    ▼
unit-tests     (ScalaTest — targeted to affected modules)
```

### Gate Properties

| Field         | Description                                  |
|---------------|----------------------------------------------|
| `description` | Human-readable explanation                   |
| `command`     | Shell command executed in the worktree        |
| `retryable`   | If `true`, agent can fix and retry on failure |

### Gate Behavior

- Gates run **sequentially** — each must pass before the next runs
- On failure of a **retryable** gate:
  - Error output is sent to the agent (LLM or Cursor CLI)
  - Agent applies a fix
  - **ALL gates re-run from the beginning** (not just the failed gate)
  - Repeat until all pass or `MAX_GATE_RETRIES` exhausted
- On failure of a **non-retryable** gate:
  - Pipeline stops immediately
  - No MR is created

### Why Re-run ALL Gates

```
❌ WRONG: retry only the failed gate

  Attempt 1: compile → ❌ (type error)
  Agent fix: changes return type
  Attempt 2: compile → ✅
             unit-tests → ❌ (return type broke test)
  Agent fix: updates test assertion (introduces typo)
  Attempt 3: unit-tests → ✅
  BUT: typo would have failed compile → BUG SHIPPED

✅ RIGHT: re-run all gates from start

  Attempt 1: compile → ❌
  Agent fix: changes return type
  Attempt 2: compile → ✅ → unit-tests → ❌
  Agent fix: updates test (introduces typo)
  Attempt 3: compile → ❌ (typo caught!)
  Agent fix: fixes typo
  Attempt 4: compile → ✅ → unit-tests → ✅ → SAFE
```

---

## 4. Incremental Validation

Incremental validation maps changed files to modules and runs only the
affected tests. This is a **speed optimization, not a safety shortcut**.

### Decision Tree

```
Files changed?
│
├── NO → skip ("No files changed")
│        result.passed = True → proceed to MR
│
└── YES
     │
     ▼
Risk check passes?
     │
     ├── NO → block ("Restricted path modified")
     │        result.passed = False → NO MR
     │
     └── YES
          │
          ▼
module_mapping configured in rules.yaml?
          │
          ├── NO → run ALL gates unchanged
          │        (safety default — can't determine scope)
          │
          └── YES
               │
               ▼
Any module affected by changed files?
               │
               ├── NO → SKIP validation entirely
               │        Only non-code files changed
               │        (README, docs, .gitignore, etc.)
               │        result.passed = True → proceed to MR
               │
               └── YES
                    │
                    ▼
               Run TARGETED gates
                 compile: always full (cross-deps)
                 tests:   only affected modules
```

### Concrete Examples (zdpas project)

> **Note:** The following examples are specific to the zdpas Scala/Java project.
> Your repository will have its own `.saturn/` configuration.

| Changed Files | Modules | Test Scope | Tests | Time |
|---|---|---|---|---|
| `source/.../transformer/Filter.scala` | `{transformer}` | `-w com.zoho.dpaas.transformer` | ~85 | ~45s |
| `source/.../export/Excel.scala` + `source/.../common/Utils.scala` | `{export, common}` | `-w com.zoho.dpaas.export -w com.zoho.dpaas.common` | ~250 | ~2min |
| `test/resources/import/excel/data.xlsx` | `{import}` | `-w com.zoho.dpaas.import` | ~120 | ~1min |
| `README.md` | `{}` (empty) | SKIP — no code modules | 0 | ~0s |
| No `.saturn/` config at all | (unknown) | Full suite `-w com.zoho.dpaas` | 3868 | ~10min |

### CI vs Agent — Test Scope

| | CI Pipeline | Saturn Agent |
|---|---|---|
| **Trigger** | Every commit / MR | Per coding task |
| **Compile** | Full (always) | Full (always — cross-deps) |
| **Tests** | Full: `-w com.zoho.dpaas` (3868) | Targeted: `-w com.zoho.dpaas.parser` (~47) |
| **Time** | ~10 min | ~30s - 2min |
| **Purpose** | Final validation before merge | Fast feedback for retry loop |

---

## 5. Gate Retry Loop (Self-Healing)

When a gate fails due to the agent's code changes, Saturn feeds the error
back to the agent and retries. The gate failure is a **fix signal, not a stop signal**.

### Retry Flow

```
Attempt 1:
  🚧 setup         → ✅
  🚧 compile       → ❌ FilterTransform.scala:42 type mismatch
     │
     ▼
  🔧 Agent fixes FilterTransform.scala
     │
     ▼ (re-run ALL gates from beginning)

Attempt 2:
  🚧 setup         → ✅
  🚧 compile       → ✅ (compile fixed)
  🚧 compile-tests → ✅
  🚧 unit-tests    → ❌ FilterTransformTest FAILED: expected 5 got 3
     │
     ▼
  🔧 Agent fixes FilterTransform.scala (logic error)
     │
     ▼ (re-run ALL gates from beginning)

Attempt 3:
  🚧 setup         → ✅
  🚧 compile       → ✅
  🚧 compile-tests → ✅
  🚧 unit-tests    → ✅

  ✅ All gates passed (after 2 retries)
     │
     ▼
  📦 commit → push → MR
```

### Fix Callback

The agent receives structured, gate-specific error prompts:

- **compile failure**: "There are compilation errors in the code you changed..."
- **unit-tests failure**: "Tests are failing after your changes..."
- **compile-tests failure**: "Test sources failed to compile, you may have broken the test API..."

This helps the LLM produce targeted fixes rather than guessing.

---

## 6. Repository Configuration (.saturn/)

Each repository Saturn operates on can provide a `.saturn/` configuration directory.
If `.saturn/` doesn't exist, Saturn auto-discovers the project type and uses defaults.

### Directory Structure

```
your-repo/
├── src/                     # Your source code
├── tests/                   # Your tests
└── .saturn/
    ├── gates.yaml           # Deterministic validation steps
    ├── rules.yaml           # Incremental validation (module mapping)
    └── risk.yaml            # Patch safety limits
```

---

### Example: Generic Python Project

**`.saturn/gates.yaml`**
```yaml
version: 1

gates:
  lint:
    description: "Run linting"
    command: "ruff check ."
    retryable: true

  test:
    description: "Run tests"
    command: "pytest tests/ -v"
    retryable: true
```

**`.saturn/risk.yaml`**
```yaml
version: 1

risk_limits:
  max_files_changed: 20
  max_lines_changed: 1000

restricted_paths:
  - .github/
  - .saturn/
```

---

### Example: zdpas (Scala/Java Project)

> The following is a complete example for the zdpas Scala/Java project with
> joint compilation and ScalaTest.

**`.saturn/gates.yaml`**

```yaml
version: 1

gates:

  setup:
    description: "Extract cached build into DPAAS_HOME"
    command: |
      set -e
      if [[ ! -f build/ZDPAS/output/dpaas.tar.gz ]]; then
        echo "ERROR: dpaas.tar.gz not found"; exit 1
      fi
      rm -rf "$DPAAS_HOME/zdpas" 2>/dev/null || true
      tar -xf build/ZDPAS/output/dpaas.tar.gz -C "$DPAAS_HOME"
      mkdir -p "$DPAAS_HOME/zdpas/spark/app_blue"
      cp "$DPAAS_HOME/zdpas/spark/jars/dpaas.jar" \
         "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar"
      echo "✅ Setup complete"
    retryable: false

  compile:
    description: "Joint-compile all Java and Scala sources"
    command: |
      set -e
      find ./source -name "*.java" -type f \
        | grep -v -E '^./source/(Main|Test)\.' > all_java.txt
      find ./source -name "*.scala" -type f \
        | grep -v -E '^./source/(Main|Test|Generate)' > scala_files.txt
      cat all_java.txt scala_files.txt > all_sources.txt

      mkdir -p compiled_classes
      scalac -cp "$DPAAS_HOME/zdpas/spark/jars/*:$DPAAS_HOME/zdpas/spark/lib/*" \
        -J-Xmx2g -d compiled_classes @all_sources.txt
      javac -cp "compiled_classes:$DPAAS_HOME/zdpas/spark/jars/*:\
      $DPAAS_HOME/zdpas/spark/lib/*" \
        -sourcepath ./source -d compiled_classes @all_java.txt

      jar cf dpaas.jar -C compiled_classes .
      cp dpaas.jar "$DPAAS_HOME/zdpas/spark/app_blue/dpaas.jar"
      echo "✅ Compile complete"
    retryable: true

  compile-tests:
    description: "Compile test sources"
    command: |
      set -e
      test -f dpaas.jar || { echo "ERROR: dpaas.jar missing"; exit 1; }

      find ./test/source -name "*.scala" -type f > source_test.txt
      mkdir -p test_compiled_classes
      scalac -cp "dpaas.jar:$DPAAS_HOME/zdpas/spark/jars/*" \
        -J-Xmx2g -d test_compiled_classes @source_test.txt

      jar cf dpaas_test.jar -C test_compiled_classes .
      echo "✅ Test compile complete"
    retryable: true

  unit-tests:
    description: "Run ScalaTest — targeted to affected modules"
    command: |
      set -e
      TEST_W_FLAGS="{test_w_flags}"
      if [[ -z "$TEST_W_FLAGS" ]]; then
        echo "⚠️ No test scope — skipping"
        exit 0
      fi
      echo "🔬 Running: $TEST_W_FLAGS"

      java -cp "./dpaas_test.jar:./dpaas.jar:$DPAAS_HOME/zdpas/spark/jars/*" \
        -Xmx3g org.scalatest.tools.Runner \
        -R ./dpaas_test.jar $TEST_W_FLAGS -oC
    retryable: true
```

### `.saturn/rules.yaml`

```yaml
version: 1

incremental:
  compile_strategy: "full"

  module_mapping:
    # Source modules
    - path: "source/com/zoho/dpaas/parser"
      module: "parser"
    - path: "source/com/zoho/dpaas/datatype"
      module: "datatype"
    - path: "source/com/zoho/dpaas/transformer"
      module: "transformer"
    - path: "source/com/zoho/dpaas/transform"
      module: "transform"
    - path: "source/com/zoho/dpaas/udf"
      module: "udf"
    - path: "source/com/zoho/dpaas/common"
      module: "common"
    - path: "source/com/zoho/dpaas/import"
      module: "import"
    - path: "source/com/zoho/dpaas/export"
      module: "export"

    # Test modules
    - path: "test/source/com/zoho/dpaas/parser"
      module: "parser"
    - path: "test/source/com/zoho/dpaas/datatype"
      module: "datatype"

  test_mapping:
    parser:
      pattern: "com.zoho.dpaas.parser"
    datatype:
      pattern: "com.zoho.dpaas.datatype"
    transformer:
      pattern: "com.zoho.dpaas.transformer"
    transform:
      pattern: "com.zoho.dpaas.transform"
    udf:
      pattern: "com.zoho.dpaas.udf"
    common:
      pattern: "com.zoho.dpaas.common"
    import:
      pattern: "com.zoho.dpaas.import"
    export:
      pattern: "com.zoho.dpaas.export"
```

### `.saturn/risk.yaml`

```yaml
version: 1

risk_limits:
  max_files_changed: 25
  max_lines_changed: 1500

restricted_paths:
  - build/
  - .gitlab-ci.yml
  - .saturn/
  - infra/

restricted_files:
  - build/ant.properties
  - .env
  - secrets.yml
```

---

## 7. Dev Environment Setup

### 7.1 Prerequisites

| Tool | Version | Purpose |
|---|---|---|
| Python | 3.11+ | Saturn server, worker, agent |
| Git | 2.30+ | Worktree support |
| Cursor CLI | latest | Primary coding engine (recommended) |

**Optional** (only for zdpas or similar Scala/Java projects):
| Tool | Version | Purpose |
|---|---|---|
| Java | 8 or 11 | Compilation + test runtime |
| Scala | 2.12.x | Compilation |
| Ant | 1.10+ | Bootstrap build |

### 7.2 Clone and Setup

```bash
# Clone Saturn
git clone https://github.com/your-org/saturn.git
cd saturn

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .

# Install Cursor CLI (recommended)
curl https://cursor.com/install -fsS | bash
```

### 7.3 Environment Configuration

```bash
cp saturn.env.example saturn.env
```

Edit `saturn.env`:

```env
# ── Coding Engine ── (cursor is default and recommended)
LLM_PROVIDER=cursor
CURSOR_CLI_PATH=agent
CURSOR_TIMEOUT_SECONDS=600

# ── Or use legacy LLM directly: ──
# LLM_PROVIDER=ollama
# OLLAMA_BASE_URL=http://localhost:11434
# OLLAMA_MODEL=qwen2.5:7b

# ── Target Repository ──
REPO_URL=https://gitlab.yourcompany.com/group/repo.git
REPO_LOCAL_PATH=/data/saturn/repo
WORKTREE_BASE_DIR=/data/saturn/tasks

# ── GitLab ──
GITLAB_URL=https://gitlab.yourcompany.com
GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
GITLAB_PROJECT_ID=123
GITLAB_DEFAULT_BRANCH=main

# ── Zoho Cliq (optional — for bot integration) ──
CLIQ_BOT_UNIQUE_NAME=saturnbot
CLIQ_BOT_ZAPIKEY=1001.xxxxxxxxxxxx.xxxxxxxxxxxx
CLIQ_BOT_API_URL=https://cliq.zoho.in/api/v2/channelsbyname/yourchannel/message
CLIQ_CHANNEL_UNIQUE_NAME=yourchannel
CLIQ_CHAT_ID=CT_xxxxxxxxxxxx_xxxxxxxxxxxx

# ── Agent Limits ──
MAX_LOOP_ITERATIONS=20
```

### 7.4 Verify Cursor CLI

```bash
# Verify Cursor CLI is installed
agent --version

# Test it works (optional)
agent --print "What version are you?"
```

---

## 8. Running Saturn Locally

### 8.1 Start the Server

```bash
# Start Saturn Server (includes background worker)
python main.py

# Or with uvicorn directly
uvicorn server.app:create_app --factory --host 0.0.0.0 --port 8000 --reload

# Verify
curl http://localhost:8000/health
# {"status":"ok","agent":"saturn","version":"0.1.0","repo":"..."}
```

### 8.2 Submit a Task

**Via REST API** (easiest for testing):
```bash
curl -X POST http://localhost:8000/tasks/submit \
  -H "Content-Type: application/json" \
  -d '{"description": "Fix date parsing for ISO-8601 format", "task_type": "bug_fix"}'
```

**Via Zoho Cliq** (production):
Send a message to the configured Cliq channel and the bot picks it up.

### 8.3 Watch the Execution

```
🪐 Saturn watching: https://gitlab.yourcompany.com/group/repo.git
🤖 Saturn agent worker started

═══════════════════════════════════════════════════════════════
📋 Task: Fix date parsing for ISO-8601 format
🆔 Branch: saturn/bugfix/fix-date-parsing-a1b2c3
═══════════════════════════════════════════════════════════════

📥 Creating worktree...
🌿 Worktree created: /data/saturn/tasks/saturn-bugfix-fix-date-parsing-a1b2c3

🖥️  Delegating to Cursor CLI...
  ✅ Cursor CLI finished — 2 files changed

────────────────────────────────────────
🚧 Running deterministic gates...
────────────────────────────────────────
  📂 Using .saturn/ repo config
  📋 2 files changed
  🛡️  Risk check: ✅ passed
  📦 Affected modules: parser

  🚧 Gate [compile]: Compile sources
  ✅ [compile] passed
  🚧 Gate [unit-tests]: Run tests
  ✅ [unit-tests] passed

  ✅ All gates passed

📦 Finalizing: commit → push → MR...
  💾 Committing: fix: Fix date parsing for ISO-8601 format
  🚀 Pushing to origin...
  📝 Creating MR...
  ✅ MR created: https://gitlab.yourcompany.com/.../merge_requests/456

✅ Task completed in 95.2s
🧹 Worktree removed
```

### 8.4 Run Tests

```bash
pytest tests/ -v
```

---

## 9. Deployment (GitLab Runner)

### 9.1 Runner Setup

Register a dedicated GitLab runner for Saturn:

```bash
gitlab-runner register \
  --url https://gitlab.yourcompany.com \
  --registration-token $REGISTRATION_TOKEN \
  --executor shell \
  --tag-list saturn,shell \
  --description "Saturn Agent Runner" \
  --run-untagged=false
```

### 9.2 Runner Machine Requirements

```bash
# Directory structure
/opt/saturn/
├── repo.git/               # bare clone (persistent across jobs)
├── worktrees/              # task worktrees (ephemeral)
│   └── .build_cache/       # shared build cache (persistent)
└── venv/                   # Python virtualenv (persistent)

# Permissions
sudo chown -R gitlab-runner:gitlab-runner /opt/saturn
sudo chown -R gitlab-runner:gitlab-runner /opt/dpaas
```

### 9.3 GitLab CI Job

```yaml
# saturn_worker.yml — add to your project's .gitlab-ci.yml

saturn_worker:
  tags:
    - saturn
    - shell
  rules:
    - if: '$SATURN_WORKER == "true"'
      when: always
    - when: never
  timeout: 12h
  variables:
    GIT_STRATEGY: none
    DPAAS_HOME: "/opt/dpaas"
    BUILD_FILE_HOME: "/home/test/git-runner/ref"
  before_script:
    - 'source /opt/saturn/venv/bin/activate'
    - 'echo "🪐 Saturn Worker starting on $(hostname)"'
  script:
    - 'python main.py'
  after_script:
    - 'echo "🪐 Saturn Worker stopped"'
```

---

## 10. CI Pipeline Integration

The CI pipeline and Saturn gates serve different purposes:

| | CI Pipeline | Saturn Gates |
|---|---|---|
| **When** | Every commit/MR | Per agent task |
| **Build** | Full Ant build or cache | Cache only (seeded) |
| **Compile** | Full | Full (cross-deps) |
| **Tests** | All 3868 tests | Targeted (~50-250) |
| **Time** | ~10 min | ~30s-2min |
| **Purpose** | Final validation | Fast feedback loop |

The CI pipeline is the **final gate before merge**. Saturn gates are the
**fast feedback loop for the agent's retry cycle**.

---

## 11. API Reference

### Submit Task

```bash
POST /tasks/submit
Content-Type: application/json

{
  "description": "Fix the login timeout bug in AuthService",
  "task_type": "bug_fix",    # bug_fix, feature, refactor, docs, unknown
  "priority": "medium",       # low, medium, high
  "branch_name": ""           # optional — auto-generated if empty
}
```

Response:
```json
{
  "status": "queued",
  "task_id": "SATURN-A1B2C3D4",
  "description": "Fix the login timeout bug in AuthService",
  "task_type": "bug_fix",
  "priority": "medium",
  "queue_size": 1
}
```

### Health Check

```bash
GET /health
```

Response:
```json
{
  "status": "ok",
  "agent": "saturn",
  "version": "0.1.0",
  "repo": "https://gitlab.yourcompany.com/group/repo.git",
  "queue_size": 0
}
```

### Zoho Cliq Webhook

```bash
POST /webhook/cliq
```

Receives messages from Zoho Cliq bot. The bot parses task descriptions from
channel messages and queues them for processing.

---

## 12. Configuration Reference

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| **Coding Engine** | | |
| `LLM_PROVIDER` | `cursor` | `cursor` (recommended), `ollama`, or `anthropic` |
| `CURSOR_CLI_PATH` | `agent` | Path to Cursor Agent CLI binary |
| `CURSOR_TIMEOUT_SECONDS` | `600` | Max time per Cursor invocation |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API URL (if using ollama) |
| `OLLAMA_MODEL` | `qwen2.5:7b` | Ollama model name |
| `ANTHROPIC_API_KEY` | | Anthropic API key (if using anthropic) |
| **Repository** | | |
| `REPO_URL` | (required) | GitLab repo HTTPS URL |
| `REPO_LOCAL_PATH` | `/data/saturn/repo` | Path to bare clone |
| `WORKTREE_BASE_DIR` | `/data/saturn/tasks` | Base dir for worktrees |
| **GitLab** | | |
| `GITLAB_URL` | | GitLab instance URL |
| `GITLAB_TOKEN` | (required) | GitLab personal access token |
| `GITLAB_PROJECT_ID` | | GitLab project ID (numeric or path) |
| `GITLAB_DEFAULT_BRANCH` | `main` | Default branch name |
| **Zoho Cliq** | | |
| `CLIQ_BOT_UNIQUE_NAME` | | Bot unique name (e.g. `saturnbot`) |
| `CLIQ_BOT_ZAPIKEY` | | Bot ZAPI key (no OAuth needed) |
| `CLIQ_BOT_API_URL` | | Channel message API URL |
| `CLIQ_CHANNEL_UNIQUE_NAME` | | Channel unique name |
| `CLIQ_CHAT_ID` | | Chat ID for thread replies |
| **Server** | | |
| `SERVER_HOST` | `0.0.0.0` | Server bind host |
| `SERVER_PORT` | `8000` | Server bind port |
| **Agent** | | |
| `MAX_LOOP_ITERATIONS` | `20` | Max agent loop iterations |
| `THINKING_BUDGET_TOKENS` | `10000` | LLM thinking budget (legacy mode) |

### `.saturn/gates.yaml` Schema

```yaml
version: 1

gates:
  <gate-name>:
    description: ""    # Human-readable explanation
    command: ""        # Shell command (multi-line supported)
    retryable: false   # Agent can fix and retry on failure

# Substitution tokens in gate commands:
#   {modules}        → "parser datatype" (space-separated module names)
#   {test_patterns}  → "com.zoho.dpaas.parser com.zoho.dpaas.datatype" (test packages)
```

### `.saturn/rules.yaml` Schema

```yaml
version: 1

incremental:
  compile_strategy: "full"   # "full" or "incremental"

  module_mapping:
    - path: "source/..."     # File path prefix
      module: "module-name"  # Logical module name

  test_mapping:
    module-name:
      pattern: "com.zoho..." # ScalaTest package for -w flag
```

### `.saturn/risk.yaml` Schema

```yaml
version: 1

risk_limits:
  max_files_changed: 25
  max_lines_changed: 1500

restricted_paths:
  - build/
  - infra/

restricted_files:
  - .env
  - secrets.yml
```

---

## 13. Troubleshooting

### Cursor CLI not found

```
FileNotFoundError: [Errno 2] No such file or directory: 'agent'
```

**Cause**: Cursor CLI not installed or not in PATH.

**Fix**:
```bash
curl https://cursor.com/install -fsS | bash
# Or specify full path in saturn.env:
CURSOR_CLI_PATH=/path/to/agent
```

### Repository not configured

```
RuntimeError: REPO_URL not configured
```

**Cause**: Saturn needs a repo to watch.

**Fix**: Set `REPO_URL` in saturn.env:
```env
REPO_URL=https://gitlab.yourcompany.com/group/repo.git
```

### GitLab authentication failed

```
401 Unauthorized
```

**Cause**: Invalid or expired GitLab token.

**Fix**: Generate a new token with `api` scope:
```env
GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
```

### Worktree locked

```
fatal: 'saturn/task/...' is already checked out
```

**Fix**: Clean stale worktrees:
```bash
git -C /data/saturn/repo worktree prune
rm -rf /data/saturn/tasks/saturn-*
```

### Gate timeout

```
Gate [compile] timed out after 120s
```

**Fix**: Increase timeout in gate executor (default is 120s per gate).

### Cliq bot not responding

**Cause**: ZAPI key not configured or invalid.

**Fix**: Get your ZAPI key from Cliq Admin → Bots → Your Bot → API:
```env
CLIQ_BOT_ZAPIKEY=1001.xxxxxxxxxxxx.xxxxxxxxxxxx
```

---

### zdpas-specific Issues

These issues are specific to the zdpas Scala/Java project:

**Build cache missing**
```
ERROR: dpaas.tar.gz not found
```
Seed the build cache from CI artifacts or run the Ant bootstrap build.

**Aspose license missing**
```
⚠️ LICENSE MISSING
```
The setup gate should preserve license files. Verify they exist in dpaas.tar.gz.

**ZDDateParser NoClassDefFoundError**
```
java.lang.NoClassDefFoundError: com/zoho/dpaas/parser/dateparser/ZDDateParser
```
The compile gate must run `scalac` then `javac` — both steps required for joint compilation.

---

## 🔒 Safety

- **Blocked commands**: `rm -rf /`, `DROP TABLE`, fork bombs, etc.
- **Max loop limit**: 20 iterations (configurable via `MAX_LOOP_ITERATIONS`)
- **Repetition detection**: Force-breaks LLM tool loops after 2 nudges
- **Worktree isolation**: Each task runs in its own directory
- **Path sandboxing**: Tools cannot access files outside the worktree
- **Auto-verify**: Tests run after every edit — self-heals failures
- **Force-push safety**: Only `saturn/` prefixed branches are force-pushed

---

## Design Principles

### Deterministic Validation

Gates produce the **same result for identical inputs**. No flaky tests,
no network-dependent checks, no randomness.

### Fast Feedback

Agent gates target **30 seconds to 2 minutes**. Heavy validation stays
in CI. The agent needs fast feedback to iterate effectively.

### Repository Ownership

Repositories define their own correctness rules via `.saturn/`. Saturn is
a **generic execution engine** — it never hardcodes project-specific logic.

### Never Skip Code Validation

If code modules are affected, validation **always runs**. Incremental
narrowing is a speed optimization, not a safety shortcut.

### Self-Healing Over Stopping

When a gate fails, the agent gets the error and tries to fix it. The pipeline
only stops when:
- A non-retryable gate fails
- Max retries exhausted
- Agent explicitly can't produce a fix

---

## 📜 License

MIT

---

*Saturn — Autonomous coding, deterministic validation.*
