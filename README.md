# 🤖 Saniyan — Autonomous Coding Agent

**Saniyan** is a fully autonomous coding agent that monitors your **Zoho Cliq** channel for issues and feature requests, then solves them end-to-end — reading code, reasoning about the problem, making edits, running tests, and opening Pull Requests — all without human intervention.

Inspired by [Stripe's Minions](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2) — one-shot, end-to-end coding agents.

---

## ⚡ How It Works

```
Zoho Cliq Channel                    GitHub
     │                                 │
     │  "Fix the login timeout bug"    │
     ▼                                 │
┌─────────────────────┐                │
│  Saniyan Webhook    │                │
│  POST /webhook/cliq │                │
└────────┬────────────┘                │
         ▼                             │
┌─────────────────────┐                │
│  Task Queue         │                │
│  Parse → Classify   │                │
└────────┬────────────┘                │
         ▼                             │
┌─────────────────────────────────┐    │
│  🧠 Agentic Loop               │    │
│                                 │    │
│  OBSERVE → read files, git     │    │
│  PLAN    → Claude reasons      │    │
│  ACT     → edit, run commands  │    │
│  VERIFY  → run tests           │    │
│  ITERATE → self-heal if fail   │    │
│  COMMIT  → git commit + push   │────┼──→ Opens PR
└─────────────────────────────────┘    │
         │                             │
         ▼                             │
   📨 Report back to Cliq             │
```

## 🏗 Architecture

| Layer | Component | Purpose |
|-------|-----------|---------|
| **Interface** | FastAPI webhook server | Receives messages from Zoho Cliq |
| **Dispatcher** | Task queue + worker | Queues tasks, isolates workspaces |
| **Brain** | Claude API + extended thinking | Reasons about problems, decides actions |
| **Tools** | File, Terminal, Git, GitHub, Search | How the agent acts on code |
| **Memory** | Context snapshots + action log | What the agent knows and remembers |
| **Safety** | Blocked commands, max loops, dry-run | Prevents catastrophic actions |

## 📁 Project Structure

```
saniyan/
├── main.py                  # Entry point (server or CLI)
├── config.py                # Settings from .env
├── pyproject.toml           # Dependencies
│
├── server/                  # Webhook API layer
│   ├── app.py               # FastAPI app factory
│   ├── models.py            # Pydantic models
│   └── routes/
│       ├── cliq_webhook.py  # POST /webhook/cliq
│       └── health.py        # GET /health
│
├── dispatcher/              # Task orchestration
│   ├── queue.py             # Async task queue
│   ├── worker.py            # Background task processor
│   └── workspace.py         # Repo clone + isolation
│
├── agent/                   # Core agentic loop
│   ├── agent.py             # Main loop (observe→plan→act→verify)
│   ├── brain.py             # Claude API wrapper
│   ├── context.py           # Workspace snapshot builder
│   ├── memory.py            # Action log + history
│   └── prompts.py           # System prompt + hard-problem addon
│
├── tools/                   # Tool implementations
│   ├── registry.py          # Tool schemas + executor router
│   ├── filesystem.py        # read, edit, create files
│   ├── terminal.py          # Shell command execution
│   ├── git.py               # Git operations
│   ├── github.py            # GitHub PR/issue API
│   └── search.py            # Code search (grep/ripgrep)
│
├── integrations/            # External services
│   └── cliq.py              # Zoho Cliq messaging
│
├── utils/
│   └── logging.py           # Structured logging
│
└── tests/                   # Test suite
    ├── test_agent.py
    ├── test_tools.py
    ├── test_terminal.py
    └── test_webhook.py
```

## 🚀 Quickstart

### 1. Clone and install

```bash
git clone https://github.com/Raviston6296/saniyan.git
cd saniyan
pip install -e ".[dev]"
# or
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your keys:
#   ANTHROPIC_API_KEY=sk-ant-...
#   GITHUB_TOKEN=ghp_...
#   CLIQ_WEBHOOK_TOKEN=...
#   CLIQ_BOT_API_URL=...
#   CLIQ_AUTH_TOKEN=...
```

### 3. Run the server (webhook mode)

```bash
python main.py
# Server starts at http://0.0.0.0:8000
# Configure Zoho Cliq bot to POST to /webhook/cliq
```

### 4. Run a single task (CLI mode)

```bash
# Against a GitHub repo
python main.py --task "Fix the failing tests" --repo Raviston6296/my-app

# Against the current directory
python main.py --local --task "Add error handling to all API routes"

# Dry run (no file writes)
python main.py --local --task "Refactor auth module" --dry-run
```

### 5. Run tests

```bash
pytest tests/ -v
```

## 🔗 Zoho Cliq Setup

1. Go to **Zoho Cliq** → **Bots** → **Create Bot**
2. Set the bot's **Webhook URL** to: `https://your-server.com/webhook/cliq`
3. Copy the **verification token** → put in `.env` as `CLIQ_WEBHOOK_TOKEN`
4. For sending messages back, set up **OAuth** and configure `CLIQ_BOT_API_URL` and `CLIQ_AUTH_TOKEN`

### Example Cliq messages Saniyan understands:

```
Fix the login timeout bug in Raviston6296/backend
Add rate limiting to all API routes
Refactor the auth module to use async/await
Write tests for the utils.py functions
Debug why CI is failing on main branch
Find and fix security vulnerabilities
```

## 🧠 How the Agent Thinks

For **simple tasks** (add endpoint, fix typo), Saniyan acts fast — reads files, makes edits, runs tests.

For **hard problems** (race conditions, architecture decisions, mystery bugs), Saniyan enables **extended thinking** — Claude reasons step-by-step internally before taking any action:

| Problem Type | Strategy | Thinking Budget |
|---|---|---|
| 🐛 Mystery Bug | Bisect → isolate → hypothesis tree | 10,000 tokens |
| ⚡ Race Condition | Log → reproduce → trace → mutex | 16,000 tokens |
| 🏗 Architecture | Read all → map deps → compare designs | 16,000 tokens |
| 📉 Performance | Profile → hotspot → measure before/after | 8,000 tokens |
| ♻️ Large Refactor | Map usages → plan path → change bottom-up | 10,000 tokens |

## 🔒 Safety

- **Blocked commands**: `rm -rf /`, `DROP TABLE`, fork bombs, etc.
- **Max loop limit**: Agent stops after 20 iterations (configurable)
- **Workspace isolation**: Each task runs in its own temp directory
- **Path sandboxing**: Tools cannot access files outside the workspace
- **Dry-run mode**: Preview all changes without writing to disk
- **Auto-verify**: Tests run after every edit — agent self-heals failures

## 📜 License

MIT

---

Built with 🧠 Claude + ☕ caffeine by the Saniyan team.

