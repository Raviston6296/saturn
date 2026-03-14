# 🖥️ Saturn VM Setup Guide

This guide provides step-by-step instructions to deploy Saturn on a VM,
including full setup for **Goose (by Block)** as the AI coding orchestrator.

---

## 📋 Prerequisites Checklist

| Component | Required | Notes |
|-----------|----------|-------|
| Python 3.11+ | ✅ | |
| Git 2.30+ | ✅ | |
| Goose CLI (Block) | ✅ (recommended) | Open-source AI agent |
| Cursor CLI | Optional | Alternative to Goose |
| GitLab Token | ✅ | For MR creation |
| DPAAS tar files | ✅ (ZDPAS) | `dpaas.tar.gz`, `dpaas_test.tar.gz` |
| Zoho Cliq Bot | Optional | For messaging integration |

---

## 🪿 Goose (by Block) Setup

Goose is the recommended AI coding engine for Saturn.  It is open-source,
extensible via MCP (Model Context Protocol), and integrates deeply with the
Saturn ZDPAS toolset.

### Install Goose

```bash
# Option A — official installer (recommended)
curl -fsSL https://github.com/block/goose/releases/latest/download/goose-installer.sh | bash

# Option B — via pip
pip install goose-ai

# Verify installation
goose --version
```

### Configure Goose Provider

Goose supports multiple LLM providers. Set your preferred provider:

```bash
# Using Anthropic Claude (recommended)
export GOOSE_PROVIDER=anthropic
export GOOSE_MODEL=claude-3-5-sonnet-20241022
export ANTHROPIC_API_KEY=sk-ant-...

# Using OpenAI
export GOOSE_PROVIDER=openai
export GOOSE_MODEL=gpt-4o
export OPENAI_API_KEY=sk-...

# Using Ollama (local, no API key needed)
export GOOSE_PROVIDER=ollama
export GOOSE_MODEL=qwen2.5:14b
```

Add these to your shell profile (`~/.bashrc` or `~/.zshrc`).

### Saturn Auto-Configures Goose

When Saturn starts with `LLM_PROVIDER=goose` **or** `LLM_PROVIDER=cursor+goose`, it automatically:

1. Creates the `saturn-zdpas` Goose profile at `~/.config/goose/profiles.yaml`
2. Registers the Saturn MCP server as a Goose extension at `~/.config/goose/config.yaml`

This gives Goose access to Saturn's custom ZDPAS tools:

| Tool | Tier | Description |
|------|------|-------------|
| `compile_quick` | 1 | Fast incremental compile (5–30 s) |
| `compile_module` | 1 | Compile a whole module |
| `find_similar_code` | — | Find existing patterns before writing |
| `get_test_template` | — | Copy-and-adapt test scaffold |
| `run_module_tests` | 2 | Targeted ScalaTest run (2–10 min) |
| `sync_resources` | — | Confirm resource file visibility |
| `search_code` | — | Grep across all Scala/Java sources |
| `get_module_context` | — | Module files, classes, test suites |
| `get_project_info` | — | ZDPAS structure overview |
| `get_changed_files` | — | Track agent-modified files |
| `get_dpaas_env` | — | DPAAS_HOME and jar status |

---

## 🔀 Hybrid Mode: Cursor LLM + Goose Orchestration

`LLM_PROVIDER=cursor+goose` combines the best of both engines in a single task flow.

### How It Works

```
Task received
     │
     ▼
Goose pre-flight           ← project structure + DPAAS env check
     │
     ▼
Cursor coding phase        ← LLM code generation (Cursor's strength)
  ┌─ reads/edits files
  └─ uses ZDPAS context injected from Goose's pre-flight
     │
     ▼
Goose validation phase     ← MCP-powered validation (Goose's strength)
  ├─ compile_quick() on all changed files  (Tier 1 — 5–30 s)
  └─ run_module_tests() for affected modules  (Tier 2 — 2–10 min)
     │
     ├── PASS ──→ Saturn Tier-1 gate pipeline (risk check + static validation)
     └── FAIL ──→ Cursor re-codes + Goose re-validates  (fix loop)
                   Saturn orchestrates up to 5 retry attempts
```

### Why Hybrid?

| Aspect | Cursor-only | Goose-only | Cursor+Goose |
|--------|-------------|------------|--------------|
| Code generation | ✅ Excellent | ✅ Good | ✅ Cursor's LLM |
| MCP tooling | ❌ None | ✅ Full Saturn MCP | ✅ Full Saturn MCP |
| Compile feedback | ❌ Gate only | ✅ After each edit | ✅ After coding phase |
| Test feedback | ❌ Gate only | ✅ Before finishing | ✅ Before gate |
| Context retention | ❌ None | ✅ Named sessions | ✅ Goose session |
| Gate fix quality | ✅ Strong | ✅ Contextual | ✅✅ Cursor codes + Goose validates |

### Requirements

Both binaries must be installed:
```bash
# Cursor CLI
curl https://cursor.com/install -fsS | bash

# Goose
curl -fsSL https://github.com/block/goose/releases/latest/download/goose-installer.sh | bash
```

### Health Check (Hybrid Mode)

```bash
# Verify both engines are available
agent --version    # Cursor CLI
goose --version    # Goose CLI

# Check Saturn hybrid config
python -c "
from config import settings
print(f'LLM_PROVIDER: {settings.llm_provider}')
assert settings.llm_provider == 'cursor+goose'
from agent.agent import AutonomousAgent
import unittest.mock as mock
with mock.patch('agent.goose_cli.GooseCLI._verify_cli'):
    a = mock.MagicMock()
print('✅ Hybrid config OK')
"
```

---

## 📦 DPAAS Tar Files — Where to Put Them

The ZDPAS compilation pipeline requires two tar files produced by CI/CD:

| File | Default Path | Environment Variable |
|------|-------------|---------------------|
| `dpaas.tar.gz` | `build/ZDPAS/output/dpaas.tar.gz` | `DPAAS_SOURCE_TAR` |
| `dpaas_test.tar.gz` | `build/ZDPAS/output/dpaas_test.tar.gz` | `DPAAS_TEST_TAR` |

These paths are **relative to the ZDPAS worktree root** (checked-out branch).
CI/CD places them there automatically.  For local testing you can override:

```bash
# In saturn.env or the runner VM shell profile:
export DPAAS_SOURCE_TAR=/path/to/dpaas.tar.gz
export DPAAS_TEST_TAR=/path/to/dpaas_test.tar.gz
```

### DPAAS_HOME — System Property vs Environment Variable

Saturn passes `DPAAS_HOME` to Java/Scala test runs as **both**:
- An environment variable (`DPAAS_HOME=...`) — for shell-level tools
- A JVM system property (`-DDPAAS_HOME=...`) — for Scala code in separate shell mode

Scala/Java test code should read it via `System.getProperty("DPAAS_HOME")`
(not `sys.env("DPAAS_HOME")`) when running in a separate JVM subprocess.

Set it in the runner VM shell profile for all jobs to pick it up:

```bash
# /etc/environment or ~/.bashrc on the runner VM
export DPAAS_HOME=/opt/dpaas
export BUILD_FILE_HOME=/home/gitlab-runner/build-files
```

---

## 🚀 Quick Start (5 Minutes)

```bash
# 1. Clone Saturn
git clone https://github.com/your-org/saturn.git
cd saturn

# 2. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -e .

# 4. Install Goose
curl -fsSL https://github.com/block/goose/releases/latest/download/goose-installer.sh | bash

# 5. Configure environment
cp saturn.env.example saturn.env
# Edit saturn.env — set LLM_PROVIDER=goose and your API key

# 6. Start Saturn
python main.py
```

---

## 📝 Detailed Setup Steps

### Step 1: Prepare the VM

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install Python 3.11+
sudo apt install -y python3.11 python3.11-venv python3-pip

# Install Git
sudo apt install -y git

# Verify versions
python3 --version   # Should be 3.11+
git --version       # Should be 2.30+
```

### Step 2: Create Saturn User (Optional but Recommended)

```bash
# Create dedicated user
sudo useradd -m -s /bin/bash saturn
sudo passwd saturn

# Create data directories
sudo mkdir -p /data/saturn/{repo,tasks}
sudo chown -R saturn:saturn /data/saturn

# Switch to saturn user
sudo su - saturn
```

### Step 3: Clone and Install Saturn

```bash
# Clone repository
cd /home/saturn
git clone https://github.com/your-org/saturn.git
cd saturn

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install Saturn
pip install -e .

# Verify installation
python -c "from config import settings; print('✅ Saturn installed')"
```

### Step 4: Install Goose (Block AI Agent — Recommended)

```bash
# Install Goose by Block
curl -fsSL https://github.com/block/goose/releases/latest/download/goose-installer.sh | bash

# Add to PATH
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Verify
goose --version

# Test Goose works (quick sanity check)
goose run --text "What is 2+2?" --with-builtin developer
```

**Why Goose over Cursor?**
- Open-source, no licence fee
- Extensible via MCP — Saturn injects ZDPAS-specific tools
- Named sessions keep context across gate fix retries
- Streaming output shows real-time progress

### Step 4b: Install Cursor CLI (Alternative to Goose)

```bash
# Install Cursor Agent CLI
curl https://cursor.com/install -fsS | bash

# Add to PATH (add to ~/.bashrc)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Verify
agent --version
```

### Step 5: Configure Environment

```bash
# Copy template
cp saturn.env.example saturn.env

# Edit configuration
nano saturn.env
```

**Minimum Configuration (Hybrid mode — Cursor LLM + Goose Orchestration — best of both):**

```env
# ── Coding Engine — Hybrid: Cursor codes, Goose validates ──
# Cursor handles the LLM code generation; Goose orchestrates validation
# via MCP (compile_quick, run_module_tests, find_similar_code, etc.)
LLM_PROVIDER=cursor+goose
CURSOR_CLI_PATH=agent
GOOSE_CLI_PATH=goose
GOOSE_PROVIDER=anthropic
GOOSE_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxx

# ── Target Repository (REQUIRED) ──
REPO_URL=https://gitlab.yourcompany.com/group/repo.git
REPO_LOCAL_PATH=/data/saturn/repo
WORKTREE_BASE_DIR=/data/saturn/tasks

# ── GitLab (REQUIRED for MR creation) ──
GITLAB_URL=https://gitlab.yourcompany.com
GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
GITLAB_PROJECT_ID=123
GITLAB_DEFAULT_BRANCH=main

# ── DPAAS Runtime (ZDPAS repos) ──
DPAAS_HOME=/opt/dpaas
BUILD_FILE_HOME=/home/gitlab-runner/build-files

# ── Server ──
SERVER_HOST=0.0.0.0
SERVER_PORT=8000

# ── Agent Limits ──
MAX_LOOP_ITERATIONS=20
```

**Minimum Configuration (Goose mode — recommended for pure Goose):**

```env
# ── Coding Engine — Goose (by Block) ──
LLM_PROVIDER=goose
GOOSE_CLI_PATH=goose
GOOSE_PROVIDER=anthropic
GOOSE_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxx

# ── Target Repository (REQUIRED) ──
REPO_URL=https://gitlab.yourcompany.com/group/repo.git
REPO_LOCAL_PATH=/data/saturn/repo
WORKTREE_BASE_DIR=/data/saturn/tasks

# ── GitLab (REQUIRED for MR creation) ──
GITLAB_URL=https://gitlab.yourcompany.com
GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
GITLAB_PROJECT_ID=123
GITLAB_DEFAULT_BRANCH=main

# ── DPAAS Runtime (ZDPAS repos) ──
DPAAS_HOME=/opt/dpaas
BUILD_FILE_HOME=/home/gitlab-runner/build-files
# Override tar paths if not using default CI/CD location:
# DPAAS_SOURCE_TAR=/path/to/dpaas.tar.gz
# DPAAS_TEST_TAR=/path/to/dpaas_test.tar.gz

# ── Server ──
SERVER_HOST=0.0.0.0
SERVER_PORT=8000

# ── Agent Limits ──
MAX_LOOP_ITERATIONS=20
```

**Minimum Configuration (Cursor-only mode):**

```env
LLM_PROVIDER=cursor
CURSOR_CLI_PATH=agent
# ... rest same as above
```

**Optional Zoho Cliq Integration:**

```env
# ── Zoho Cliq Bot ──
CLIQ_BOT_UNIQUE_NAME=saturnbot
CLIQ_BOT_ZAPIKEY=1001.xxxxxxxxxxxx.xxxxxxxxxxxx
CLIQ_BOT_API_URL=https://cliq.zoho.in/api/v2/channelsbyname/yourchannel/message
CLIQ_CHANNEL_UNIQUE_NAME=yourchannel
CLIQ_CHAT_ID=CT_xxxxxxxxxxxx_xxxxxxxxxxxx
```

### Step 6: Verify Configuration

```bash
# Test imports
source .venv/bin/activate
python -c "
from config import settings
print(f'REPO_URL: {settings.repo_url}')
print(f'GITLAB_URL: {settings.gitlab_url}')
print(f'LLM_PROVIDER: {settings.llm_provider}')
print(f'GOOSE_PROVIDER: {settings.goose_provider}')
print('✅ Configuration loaded')
"

# Verify Goose can connect to your LLM provider
goose run --text "Reply with OK" --with-builtin developer
```

### Step 7: Start Saturn

```bash
# Start server
source .venv/bin/activate
python main.py

# Expected output:
# 🪿  Goose CLI: /home/saturn/.local/bin/goose (0.x.x)
# 🔧  Saturn MCP registered in Goose extensions (workspace=...)
# 🪐 Saturn watching: https://gitlab.yourcompany.com/group/repo.git
# 🤖 Saturn agent worker started
# INFO:     Application startup complete.
# INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Step 8: Verify Server

```bash
# In another terminal
curl http://localhost:8000/health

# Expected response:
# {"status":"ok","agent":"saturn","version":"0.2.0","repo":"https://gitlab..."}
```

### Step 9: Test Task Submission

```bash
# Submit a test task
curl -X POST http://localhost:8000/tasks/submit \
  -H "Content-Type: application/json" \
  -d '{"description": "Add a hello world function", "task_type": "feature"}'

# Expected response:
# {"status":"queued","task_id":"SATURN-XXXXXXXX",...}
```

---

## 🔧 Production Setup

### Systemd Service

Create `/etc/systemd/system/saturn.service`:

```ini
[Unit]
Description=Saturn Autonomous Coding Agent
After=network.target

[Service]
Type=simple
User=saturn
Group=saturn
WorkingDirectory=/home/saturn/saturn
Environment="PATH=/home/saturn/saturn/.venv/bin:/home/saturn/.local/bin:/usr/bin"
ExecStart=/home/saturn/saturn/.venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable saturn
sudo systemctl start saturn
sudo systemctl status saturn
```

### Nginx Reverse Proxy (Optional)

Create `/etc/nginx/sites-available/saturn`:

```nginx
server {
    listen 80;
    server_name saturn.yourcompany.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

Enable:

```bash
sudo ln -s /etc/nginx/sites-available/saturn /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

---

## 📊 Code Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        SATURN SERVER                             │
│                   (FastAPI + Background Worker)                   │
└─────────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ POST /webhook/  │  │ POST /tasks/    │  │ GET /health     │
│     cliq        │  │     submit      │  │                 │
│                 │  │                 │  │ Returns status  │
│ Receives Cliq   │  │ Direct REST API │  │ and config info │
│ bot messages    │  │ for testing     │  │                 │
└────────┬────────┘  └────────┬────────┘  └─────────────────┘
         │                    │
         ▼                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ASYNC TASK QUEUE                             │
│              (asyncio.Queue — in-memory)                         │
│                                                                  │
│    TaskRequest { id, description, branch_name, ... }             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     TASK WORKER                                  │
│              (dispatcher/worker.py)                              │
│                                                                  │
│    1. Pull task from queue                                       │
│    2. Fetch latest from origin                                   │
│    3. Create git worktree                                        │
│    4. Run AutonomousAgent                                        │
│    5. Report results to Cliq                                     │
│    6. Cleanup worktree                                           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     REPO MANAGER                                 │
│              (dispatcher/workspace.py)                           │
│                                                                  │
│    • Maintains persistent bare clone at REPO_LOCAL_PATH          │
│    • Creates/removes worktrees at WORKTREE_BASE_DIR              │
│    • git fetch → git worktree add → git worktree remove          │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    AUTONOMOUS AGENT                              │
│                  (agent/agent.py)                                │
│                                                                  │
│    ┌────────────────────────────────────────────────────────┐   │
│    │           CODING ENGINE (choose one)                    │   │
│    │                                                         │   │
│    │  ┌─────────────────┐       ┌─────────────────────┐     │   │
│    │  │ Cursor CLI      │  OR   │ Legacy LLM          │     │   │
│    │  │ (LLM_PROVIDER=  │       │ (ollama/anthropic)  │     │   │
│    │  │  cursor)        │       │                     │     │   │
│    │  │                 │       │ brain.py +          │     │   │
│    │  │ Delegates all   │       │ tool schemas        │     │   │
│    │  │ coding to       │       │                     │     │   │
│    │  │ `agent` binary  │       │ Agentic loop with   │     │   │
│    │  └─────────────────┘       │ tool calls          │     │   │
│    │                            └─────────────────────┘     │   │
│    └────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                              ▼                                   │
│    ┌────────────────────────────────────────────────────────┐   │
│    │              DETERMINISTIC GATES                        │   │
│    │              (gates/__init__.py)                        │   │
│    │                                                         │   │
│    │  1. Load .saturn/gates.yaml from repo                   │   │
│    │  2. Run risk check (.saturn/risk.yaml)                  │   │
│    │  3. Map changed files → modules (.saturn/rules.yaml)    │   │
│    │  4. Execute gates sequentially                          │   │
│    │  5. On failure: agent fixes → retry all gates           │   │
│    └────────────────────────────────────────────────────────┘   │
│                              │                                   │
│                              ▼                                   │
│    ┌────────────────────────────────────────────────────────┐   │
│    │              AUTO-FINALIZE                              │   │
│    │                                                         │   │
│    │  If gates pass:                                         │   │
│    │    git add → git commit → git push → GitLab MR          │   │
│    └────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       GITLAB                                     │
│                                                                  │
│    • MR created via python-gitlab library                        │
│    • Auto-labels: saturn-auto                                    │
│    • CI pipeline triggered on MR                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## ✅ Component Status

| Component | File | Status | Description |
|-----------|------|--------|-------------|
| **Config** | `config.py` | ✅ Ready | Pydantic settings from saturn.env |
| **Server** | `server/app.py` | ✅ Ready | FastAPI with lifespan events |
| **Health Route** | `server/routes/health.py` | ✅ Ready | GET /health endpoint |
| **Tasks Route** | `server/routes/tasks.py` | ✅ Ready | POST /tasks/submit endpoint |
| **Cliq Webhook** | `server/routes/cliq_webhook.py` | ✅ Ready | POST /webhook/cliq endpoint |
| **Task Queue** | `dispatcher/queue.py` | ✅ Ready | asyncio.Queue (in-memory) |
| **Task Worker** | `dispatcher/worker.py` | ✅ Ready | Background task processor |
| **Repo Manager** | `dispatcher/workspace.py` | ✅ Ready | Bare clone + worktrees |
| **Agent** | `agent/agent.py` | ✅ Ready | Autonomous coding agent |
| **Cursor CLI** | `agent/cursor_cli.py` | ✅ Ready | Cursor CLI wrapper |
| **Brain** | `agent/brain.py` | ✅ Ready | Legacy LLM integration |
| **Gates Pipeline** | `gates/__init__.py` | ✅ Ready | Deterministic validation |
| **Gate Executor** | `gates/executor.py` | ✅ Ready | Sequential gate runner |
| **Risk Check** | `gates/risk.py` | ✅ Ready | Patch risk validation |
| **Incremental** | `gates/incremental.py` | ✅ Ready | Module mapping |
| **Models** | `server/models.py` | ✅ Ready | Pydantic task models |
| **Cliq Integration** | `integrations/cliq.py` | ✅ Ready | Zoho Cliq API client |
| **GitLab Tools** | `tools/gitlab.py` | ✅ Ready | MR creation via API |

---

## 🔍 Troubleshooting

### Import Errors

```bash
# Ensure you're in the virtual environment
source .venv/bin/activate

# Reinstall dependencies
pip install -e .
```

### Repository Not Configured

```
RuntimeError: REPO_URL not configured
```

Edit `saturn.env` and set `REPO_URL`.

### GitLab Token Invalid

```
401 Unauthorized
```

Generate a new token with `api` scope in GitLab.

### Cursor CLI Not Found

```
FileNotFoundError: [Errno 2] No such file or directory: 'agent'
```

```bash
# Install Cursor CLI
curl https://cursor.com/install -fsS | bash

# Or set full path in saturn.env
CURSOR_CLI_PATH=/home/saturn/.local/bin/agent
```

### Permission Denied on /data/saturn

```bash
sudo mkdir -p /data/saturn/{repo,tasks}
sudo chown -R $(whoami):$(whoami) /data/saturn
```

---

## 📞 Health Check Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Agent info |
| `/health` | GET | Health status + repo info |
| `/tasks/submit` | POST | Submit task |
| `/webhook/cliq` | POST | Cliq bot webhook |

---

## 🔄 Logs

```bash
# View systemd logs
sudo journalctl -u saturn -f

# View stdout logs (when running manually)
python main.py 2>&1 | tee saturn.log
```

---

*Saturn — Ready for deployment!*

