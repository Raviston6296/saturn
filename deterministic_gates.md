# 🪐 Saturn Repository Gate Specification

## Purpose

This document defines how a repository exposes **deterministic validation rules** for Saturn, an autonomous coding agent.

Saturn must **never guess repository rules**.
Instead, each repository explicitly declares how code should be validated before a Merge Request is created.

This specification allows Saturn to:

* validate AI-generated code
* retry fixes automatically
* ensure changes respect repository policies
* keep Saturn language-agnostic

---

## Project Structure

The following is the actual Saturn codebase layout. The `gates/` directory contains the deterministic gates implementation introduced by this feature.

```
saturn/
├── .env.example
├── .gitignore
├── README.md
├── config.py                          # Saturn configuration (pydantic-settings)
├── deterministic_gates.md             # This document
├── main.py                            # Entry point
├── pyproject.toml                     # Project metadata & dependencies
├── saturn.env.example                 # Environment template
├── test_server.py                     # Server testing script
│
├── agent/                             # Autonomous coding agent
│   ├── __init__.py                    # Exports CursorCLI, CursorResult
│   ├── agent.py                       # AutonomousAgent — main orchestrator
│   ├── brain.py                       # LLM wrapper (Ollama / Anthropic)
│   ├── context.py                     # Workspace + repo context builder
│   ├── cursor_cli.py                  # Cursor Agent CLI wrapper
│   ├── memory.py                      # Task log + persistent repo memory
│   └── prompts.py                     # System prompt & hard-problem addon
│
├── dispatcher/                        # Task dispatching & workspace management
│   ├── queue.py                       # In-process async task queue
│   ├── worker.py                      # Background task worker
│   └── workspace.py                   # RepoManager — bare clone + worktrees
│
├── gates/                             # ★ Deterministic gates (this feature)
│   ├── __init__.py                    # GatePipeline orchestrator (public API)
│   ├── config.py                      # .saturn/ config loader + auto-discovery
│   ├── executor.py                    # Sequential gate runner with retry
│   ├── incremental.py                 # Module mapping & targeted gates
│   └── risk.py                        # Patch risk checker
│
├── integrations/                      # External service integrations
│   ├── cliq.py                        # Zoho Cliq messaging API
│   └── deluge_bot_script.deluge       # Cliq bot participation handler
│
├── repo_indexer/                      # Semantic code search subsystem
│   ├── __init__.py
│   ├── config.py                      # Indexer configuration
│   ├── indexer.py                     # Repo walker, chunker, embedder
│   ├── llm.py                         # Cursor CLI integration for Q&A
│   ├── main.py                        # CLI commands (index, ask, watch, stats)
│   ├── retriever.py                   # ChromaDB search + context builder
│   └── watcher.py                     # File-system watcher for live re-indexing
│
├── server/                            # FastAPI web server
│   ├── app.py                         # Application factory + lifespan
│   ├── models.py                      # Pydantic models (tasks, Cliq payloads)
│   └── routes/
│       ├── cliq_webhook.py            # Zoho Cliq webhook endpoint
│       ├── health.py                  # Health check endpoints
│       └── tasks.py                   # Direct task submission API
│
├── tests/                             # Test suite
│   ├── test_agent.py                  # Agent brain, memory, context tests
│   ├── test_terminal.py               # Terminal tools tests
│   ├── test_tools.py                  # Filesystem tools tests
│   └── test_webhook.py                # Cliq webhook tests
│
├── tools/                             # Agent tool implementations
│   ├── filesystem.py                  # Read, edit, create files
│   ├── git.py                         # Git operations (commit, push, branch)
│   ├── gitlab.py                      # GitLab MR creation & issue reading
│   ├── registry.py                    # Tool schema registry + executor
│   ├── search.py                      # Grep-based code search
│   └── terminal.py                    # Safe shell command execution
│
└── utils/
    └── logging.py                     # Structured logging (structlog)
```

---

## Implementation Mapping

The following table maps each concept in this specification to its concrete implementation in the Saturn codebase.

| Spec Concept                 | Implementation File        | Description                                          |
| ---------------------------- | -------------------------- | ---------------------------------------------------- |
| Deterministic Gates          | `gates/executor.py`        | Sequential gate runner with retry logic              |
| Gate Configuration Loader    | `gates/config.py`          | Reads `.saturn/` config files + auto-discovery       |
| Incremental Validation       | `gates/incremental.py`     | Module mapping + targeted gate narrowing             |
| Patch Risk Rules             | `gates/risk.py`            | Patch risk checker (files, lines, restricted paths)  |
| Full Validation Pipeline     | `gates/__init__.py`        | `GatePipeline` orchestrator — public API             |
| Agent Integration            | `agent/agent.py`           | `_run_gates()` and `_gate_fix_callback()` methods    |

---

## How It Works in Saturn

The following describes the end-to-end flow from task receipt to MR creation.

```
1. Task received via Zoho Cliq webhook
   server/routes/cliq_webhook.py
          │
          ▼
2. Task queued and dispatched
   dispatcher/queue.py → dispatcher/worker.py
          │
          ▼
3. Git worktree created for isolation
   dispatcher/workspace.py  (RepoManager.create_worktree)
          │
          ▼
4. Agent edits code
   agent/agent.py  (Cursor CLI or legacy brain)
          │
          ▼
5. Deterministic gates run
   gates/__init__.py  (GatePipeline.run)
     ├── Risk check          gates/risk.py
     ├── Incremental narrow  gates/incremental.py
     └── Gate execution      gates/executor.py  (sequential, with retry)
          │
     ┌────┴────┐
   pass       fail (retryable)
     │              │
     │        agent/agent.py  (_gate_fix_callback)
     │        retry gates
     │
     ▼
6. Auto-finalize
   git commit → git push → MR via tools/gitlab.py
          │
          ▼
7. Results reported to Zoho Cliq
   integrations/cliq.py
```

---

## Key Dependencies

| Package              | Purpose                                              |
| -------------------- | ---------------------------------------------------- |
| `pyyaml`             | Parsing `.saturn/` YAML configuration files          |
| `pydantic-settings`  | Saturn's own typed configuration (`config.py`)       |
| Cursor CLI (`agent`) | AI-powered code editing via the Cursor Agent binary  |
| `python-gitlab`      | GitLab MR creation and issue reading (`tools/gitlab.py`) |

---

# Repository Contract

Every repository that Saturn operates on may optionally provide a **`.saturn/` configuration directory**.

Example repository structure:

```
repo/
├ src/
├ build.sbt
├ .gitlab-ci.yml
└ .saturn/
    ├ rules.yaml
    ├ gates.yaml
    └ risk.yaml
```

Saturn must automatically detect this directory when working inside the repository.

If `.saturn/` does not exist, Saturn falls back to **auto-discovery mode** (detecting build systems and test commands).

---

# Overview of Configuration Files

| File         | Purpose                                |
| ------------ | -------------------------------------- |
| `gates.yaml` | Defines deterministic validation steps |
| `rules.yaml` | Defines incremental validation rules   |
| `risk.yaml`  | Defines patch safety limits            |

Saturn reads these files to determine **how to validate code before opening a Merge Request**.

---

# 1. Deterministic Gates (`gates.yaml`)

This file defines the ordered pipeline of deterministic checks.

Example:

```yaml
version: 1

gates:

  format:
    description: "Ensure Scala formatting"
    command: "sbt scalafmtCheck"
    retryable: true

  lint:
    description: "Run Scala style checks"
    command: "sbt scalastyle"
    retryable: true

  compile:
    description: "Compile project"
    command: "sbt compile"
    retryable: true

  fast-tests:
    description: "Run fast unit tests"
    command: "sbt 'testOnly *Unit*'"
    retryable: true
```

## Gate Fields

| Field         | Description                                |
| ------------- | ------------------------------------------ |
| `description` | Human explanation of the gate              |
| `command`     | Shell command executed by Saturn           |
| `retryable`   | Whether Saturn may attempt automatic fixes |

---

# Gate Execution Behavior

Saturn executes gates sequentially.

Example pipeline:

```
format
 ↓
lint
 ↓
compile
 ↓
fast-tests
```

Execution rules:

1. Run the gate command inside the repository worktree.
2. Capture stdout and stderr.
3. If the gate fails:

   * If `retryable=true`, the agent attempts to fix the problem.
   * Otherwise, the task stops.

Example failure message:

```
Scalafmt check failed
src/service/UserService.scala:12 formatting violation
```

Saturn should pass this error output back to the LLM for correction.

---

# 2. Incremental Validation Rules (`rules.yaml`)

This file defines how Saturn performs **incremental validation**.

Incremental validation allows Saturn to validate only the parts of the repository affected by a change.

Example:

```yaml
version: 1

incremental:

  module_mapping:

    - path: "services/auth"
      module: "auth"

    - path: "services/billing"
      module: "billing"

  test_mapping:

    auth:
      pattern: "com.company.auth.*"

    billing:
      pattern: "com.company.billing.*"
```

## Workflow

1. Saturn calculates the patch diff:

```
git diff --name-only origin/main
```

Example output:

```
services/auth/UserService.scala
```

2. Saturn maps file paths to modules using `module_mapping`.

3. Saturn runs gates only for the affected module.

Example targeted commands:

```
sbt "project auth" compile
sbt "testOnly com.company.auth.*"
```

This dramatically reduces validation time.

---

# 3. Patch Risk Rules (`risk.yaml`)

This file defines limits to prevent dangerous patches.

Example:

```yaml
version: 1

risk_limits:

  max_files_changed: 20
  max_lines_changed: 1000

restricted_paths:

  - infra/
  - terraform/
  - database/migrations/

restricted_files:

  - .env
  - secrets.yml
```

## Behavior

If a patch violates these rules, Saturn must stop execution and require human review.

Examples:

### Too Many Files Changed

```
Files changed: 45
Limit: 20
```

### Restricted Directory Modification

```
Patch modifies: terraform/network.tf
This path is restricted.
```

---

# Saturn Validation Workflow

The complete Saturn validation loop:

```
Task received
      ↓
Agent edits code
      ↓
Compute diff
      ↓
Check risk rules
      ↓
Run deterministic gates
      ↓
 ┌───────────────┐
 │ pass          │
 │               │
 │ create MR     │
 │               │
 └───────────────┘

 ┌───────────────┐
 │ fail          │
 │               │
 │ agent fixes   │
 │               │
 │ retry gates   │
 └───────────────┘
```

Retry limits should be enforced.

Example:

```
MAX_GATE_RETRIES = 5
```

---

# CI Alignment

The CI pipeline should re-run deterministic gates to ensure consistency.

Example `.gitlab-ci.yml`:

```yaml
stages:
  - validate
  - test

saturn-check:
  stage: validate
  script:
    - ./scripts/saturn_check.sh
```

This ensures:

* local validation matches CI
* no unexpected failures after MR creation

---

# Responsibilities

| Component       | Responsibility                   |
| --------------- | -------------------------------- |
| Saturn          | Execute gates and manage retries |
| Repository      | Define correctness rules         |
| CI pipeline     | Perform full validation          |
| Human reviewers | Evaluate logic and design        |

---

# Example Saturn Execution

```
Task: "Fix login timeout bug"

Agent edits files

Changed files:
services/auth/AuthService.scala

Run gates:
format → pass
lint → pass
compile → pass
fast-tests → fail

Agent reads failure:
AuthServiceTest.testTimeout

Agent modifies code

Retry gates → pass

Push branch
Create Merge Request
```

---

# Design Principles

## Deterministic Validation

Validation must produce the same result for identical inputs.

Avoid non-deterministic checks.

---

## Fast Feedback

Gate runtime target:

```
30 seconds – 2 minutes
```

Heavy checks should remain in CI.

---

## Repository Ownership

Repositories define their own correctness rules.

Saturn must remain a **generic execution engine**.

---

# Summary

Saturn operates using repository-defined validation rules:

```
repo rules → deterministic gates → MR → CI pipeline
```

This architecture ensures:

* safe autonomous code generation
* fast validation loops
* consistent repository standards
* scalable automation across many repositories

---

End of Specification
