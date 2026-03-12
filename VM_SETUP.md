# 🖥️ Saturn VM Setup Guide

This guide provides step-by-step instructions to deploy Saturn on a VM.

---

## 📋 Prerequisites Checklist

| Component | Required | Status |
|-----------|----------|--------|
| Python 3.11+ | ✅ | - |
| Git 2.30+ | ✅ | - |
| Cursor CLI | ✅ (recommended) | - |
| GitLab Token | ✅ | - |
| Zoho Cliq Bot | Optional | - |

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

# 4. Configure environment
cp saturn.env.example saturn.env
# Edit saturn.env with your settings (see below)

# 5. Start Saturn
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

### Step 4: Install Cursor CLI (Recommended)

```bash
# Install Cursor Agent CLI
curl https://cursor.com/install -fsS | bash

# Verify installation
~/.local/bin/agent --version

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

**Minimum Configuration:**

```env
# ── Coding Engine ──
LLM_PROVIDER=cursor
CURSOR_CLI_PATH=agent

# ── Target Repository (REQUIRED) ──
REPO_URL=https://gitlab.yourcompany.com/group/repo.git
REPO_LOCAL_PATH=/data/saturn/repo
WORKTREE_BASE_DIR=/data/saturn/tasks

# ── GitLab (REQUIRED for MR creation) ──
GITLAB_URL=https://gitlab.yourcompany.com
GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
GITLAB_PROJECT_ID=123
GITLAB_DEFAULT_BRANCH=main

# ── Server ──
SERVER_HOST=0.0.0.0
SERVER_PORT=8000

# ── Agent Limits ──
MAX_LOOP_ITERATIONS=20
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
print('✅ Configuration loaded')
"
```

### Step 7: Start Saturn

```bash
# Start server
source .venv/bin/activate
python main.py

# Expected output:
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

