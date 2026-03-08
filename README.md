# 🪐 Saturn — Autonomous Coding Agent

**Saturn** is a fully autonomous coding agent that monitors your **Zoho Cliq** channel for issues and feature requests, then solves them end-to-end — reading code, reasoning about the problem, making edits, running tests, and opening Pull Requests — all without human intervention.

Inspired by [Stripe's Minions](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2) — one-shot, end-to-end coding agents.

---

## 🧠 Core Philosophy: One Instance Per Repo

Saturn is designed to **deeply understand one codebase**. Each Saturn instance:

- **Maintains a persistent bare clone** — the repo is always local, always fresh
- **Uses git worktrees per task** — each task gets an isolated branch, no full re-clone
- **Remembers past work** — persistent memory of past tasks, patterns, and learnings
- **Works in parallel** — multiple worktrees can exist simultaneously

```
┌─────────────────────────────────────────┐
│  Saturn Instance (watches owner/repo)    │
│                                          │
│  📦 Persistent Bare Clone               │
│  ├── Full repo history                   │
│  ├── saturn_memory.json (learned facts)  │
│  └── git fetch --all (stays updated)     │
│                                          │
│  🌿 Git Worktrees (one per task)         │
│  ├── tasks/SATURN-A1B2C3D4/  ← fix/xxx  │
│  ├── tasks/SATURN-E5F6G7H8/  ← feat/yyy │
│  └── (auto-cleaned after PR)             │
└─────────────────────────────────────────┘
```

## ⚡ How It Works

```
Zoho Cliq Channel                    GitHub
     │                                 │
     │  "Fix the login timeout bug"    │
     ▼                                 │
┌─────────────────────┐                │
│  Saturn Webhook     │                │
│  POST /webhook/cliq │                │
└────────┬────────────┘                │
         ▼                             │
┌─────────────────────┐                │
│  Task Queue         │                │
│  Parse → Classify   │                │
└────────┬────────────┘                │
         ▼                             │
┌─────────────────────────────────┐    │
│  📡 git fetch (update clone)   │    │
│  🌿 git worktree add (branch)  │    │
│                                 │    │
│  🧠 Agentic Loop               │    │
│  OBSERVE → repo + worktree ctx  │    │
│  PLAN    → Claude reasons       │    │
│  ACT     → edit, run commands   │    │
│  VERIFY  → run tests            │    │
│  ITERATE → self-heal if fail    │    │
│  COMMIT  → git commit + push    │────┼──→ Opens PR
│                                 │    │
│  🧹 git worktree remove        │    │
└─────────────────────────────────┘    │
         │                             │
         ▼                             │
   📨 Report back to Cliq             │
```

## 🏗 Architecture

| Layer | Component | Purpose |
|-------|-----------|---------|
| **Interface** | FastAPI webhook server | Receives messages from Zoho Cliq |
| **Dispatcher** | RepoManager + task queue + worker | Bare clone, worktree lifecycle, task processing |
| **Brain** | Claude API + extended thinking | Reasons about problems, decides actions |
| **Tools** | File, Terminal, Git, GitHub, Search | How the agent acts on code |
| **Memory** | Repo memory (persistent) + task log (ephemeral) | What Saturn knows and remembers across tasks |
| **Context** | Repo snapshot + worktree snapshot | Deep codebase awareness for every decision |
| **Safety** | Blocked commands, max loops, path sandboxing | Prevents catastrophic actions |

## 📁 Project Structure

```
saturn/
├── main.py                  # Entry point (server or CLI)
├── config.py                # Per-repo instance settings
├── pyproject.toml           # Dependencies
│
├── server/                  # Webhook API layer
│   ├── app.py               # FastAPI app (repo init on startup)
│   ├── models.py            # Pydantic models
│   └── routes/
│       ├── cliq_webhook.py  # POST /webhook/cliq
│       └── health.py        # GET /health
│
├── dispatcher/              # Repo + task orchestration
│   ├── workspace.py         # RepoManager (bare clone + worktrees)
│   ├── queue.py             # Async task queue
│   └── worker.py            # Background task processor
│
├── agent/                   # Core agentic loop
│   ├── agent.py             # Main loop (observe→plan→act→verify)
│   ├── brain.py             # Claude API wrapper
│   ├── context.py           # Repo + worktree snapshot builder
│   ├── memory.py            # Two-tier: repo memory + task log
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
└── tests/                   # 42 passing tests
    ├── test_agent.py
    ├── test_tools.py
    ├── test_terminal.py
    └── test_webhook.py
```

## 🚀 Quickstart

### 1. Clone and install

```bash
git clone https://github.com/Raviston6296/saturn.git
cd saturn
pip install -e .
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:
```env
# The repo this Saturn instance watches
REPO_URL=https://github.com/your-org/your-repo.git
REPO_LOCAL_PATH=/data/saturn/repo
WORKTREE_BASE_DIR=/data/saturn/tasks

# API keys
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_TOKEN=ghp_...
GITHUB_DEFAULT_REPO=your-org/your-repo
```

### 3. Run the server (webhook mode)

```bash
python main.py
# Saturn clones the repo (first run), then starts listening
# POST /webhook/cliq → receives tasks from Cliq
```

### 4. Run a single task (CLI mode)

```bash
# Creates a worktree, runs the agent, cleans up
python main.py --task "Fix the failing tests"

# Run against current directory (no worktree)
python main.py --local --task "Add error handling to all API routes"

# Dry run (no file writes)
python main.py --local --task "Refactor auth module" --dry-run
```

### 5. Run tests

```bash
pytest tests/ -v
```

## 🌿 Git Worktrees — Why?

| Full Clone (old) | Git Worktree (new) |
|---|---|
| Clone entire repo per task (~30s) | `git worktree add` (~0.5s) |
| No shared history | Shared bare clone = full history |
| Deleted after task | Bare clone persists = deep knowledge |
| One task at a time | Multiple worktrees in parallel |
| No memory across tasks | Persistent repo memory |

## 🧠 Persistent Memory

Saturn remembers across tasks:

```json
// saturn_memory.json (in bare clone dir)
{
  "past_tasks": [
    {
      "task_id": "saturn/fix/login-timeout",
      "date": "2026-03-08",
      "description": "Fix the login timeout bug",
      "summary": "Increased session TTL from 30m to 24h in auth.config.ts",
      "pr_url": "https://github.com/org/repo/pull/42"
    }
  ],
  "knowledge": {
    "test_framework": {"value": "jest with React Testing Library"},
    "auth_module": {"value": "Uses NextAuth.js with JWT sessions"}
  }
}
```

## 🔗 Zoho Cliq Setup

1. Go to **Zoho Cliq** → **Bots** → **Create Bot**
2. Set the bot's **Webhook URL** to: `https://your-server.com/webhook/cliq`
3. Copy the **verification token** → put in `.env` as `CLIQ_WEBHOOK_TOKEN`

### Example Cliq messages:

```
Fix the login timeout bug
Add rate limiting to all API routes
Refactor the auth module to use async/await
Write tests for the utils.py functions
Debug why CI is failing on main branch
```

## 🔒 Safety

- **Blocked commands**: `rm -rf /`, `DROP TABLE`, fork bombs, etc.
- **Max loop limit**: 20 iterations (configurable)
- **Worktree isolation**: Each task runs in its own directory
- **Path sandboxing**: Tools cannot access files outside the worktree
- **Dry-run mode**: Preview changes without writing
- **Auto-verify**: Tests run after every edit — self-heals failures
- **Stale cleanup**: Prunes orphan worktrees from crashed runs on startup

## 📜 License

MIT

---

Built with 🧠 Claude + 🪐 Saturn

