# 🪐 Saturn — Autonomous Coding Agent

**Saturn** is a fully autonomous coding agent that receives tasks via **Zoho Cliq**, a **REST API**, or the **CLI** — then solves them end-to-end: reading code, reasoning about the problem, making edits, running tests, and opening **GitLab Merge Requests** — all without human intervention.

Supports both **local LLMs** (Ollama / Qwen 2.5) and **cloud LLMs** (Anthropic Claude).

Inspired by [Stripe's Minions](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2) — one-shot, end-to-end coding agents.

---

## 🧠 Core Philosophy: One Instance Per Repo

Saturn is designed to **deeply understand one codebase**. Each Saturn instance:

- **Maintains a persistent bare clone** — the repo is always local, always fresh
- **Uses git worktrees per task** — each task gets an isolated branch, no full re-clone
- **Remembers past work** — persistent memory of past tasks, patterns, and learnings
- **Works in parallel** — multiple worktrees can exist simultaneously
- **Supports local + cloud LLMs** — Ollama (Qwen 2.5, Llama 3, etc.) or Anthropic Claude
- **Uses your internal GitLab** — clones, pushes, creates MRs on your org's GitLab

```
┌──────────────────────────────────────────────────┐
│  Saturn Instance (watches one repo)               │
│                                                    │
│  📦 Persistent Bare Clone (from GitLab)           │
│  ├── Full repo history                             │
│  ├── saturn_memory.json (learned facts)            │
│  └── git fetch --all (stays updated)               │
│                                                    │
│  🌿 Git Worktrees (one per task)                   │
│  ├── worktrees/SATURN-A1B2C3D4/  ← fix/xxx        │
│  ├── worktrees/SATURN-E5F6G7H8/  ← feat/yyy       │
│  └── (auto-cleaned after MR)                       │
│                                                    │
│  🧠 LLM Brain (pick one)                           │
│  ├── Ollama → qwen2.5:7b (local, free)             │
│  └── Anthropic → Claude Sonnet (cloud, powerful)    │
│                                                    │
│  🦊 GitLab Integration                             │
│  ├── Push branches → origin (--force for saturn/)   │
│  ├── Create Merge Requests via API                  │
│  └── Read issues for context                        │
└──────────────────────────────────────────────────┘
```

## ⚡ How It Works

```
  Zoho Cliq / REST API / CLI
          │
          │  "Fix the login timeout bug"
          ▼
  ┌─────────────────────┐
  │  Saturn Server       │
  │  POST /webhook/cliq  │
  │  POST /tasks/submit  │
  │  python main.py -t   │
  └────────┬─────────────┘
           ▼
  ┌─────────────────────┐
  │  Task Queue          │
  │  Parse → Classify    │
  │  → Generate branch   │
  └────────┬─────────────┘
           ▼
  ┌──────────────────────────────────────┐
  │  📡 git fetch (update bare clone)    │
  │  🌿 git worktree add -B (new branch) │
  │                                       │
  │  🧠 Agentic Loop (max 20 iterations) │
  │  ┌────────────────────────────────┐   │
  │  │ OBSERVE → list files, read code│   │
  │  │ PLAN    → LLM reasons          │   │
  │  │ ACT     → edit/create files     │   │
  │  │ VERIFY  → run tests             │   │
  │  │ ITERATE → self-heal if fail     │   │
  │  └────────────────────────────────┘   │
  │                                       │
  │  📦 Auto-Finalize                     │
  │  ├── git add -A && git commit         │
  │  ├── git push --force origin branch ──┼──→ GitLab
  │  └── Create Merge Request via API  ───┼──→ MR link
  │                                       │
  │  🧹 Cleanup worktree                  │
  └──────────────────────────────────────┘
           │
           ▼
    📨 Report back to Cliq (with MR link)
```

## 🏗 Architecture

| Layer | Component | Purpose |
|-------|-----------|---------|
| **Interface** | FastAPI server | Webhook (`/webhook/cliq`), REST API (`/tasks/submit`), Health (`/health`) |
| **Dispatcher** | RepoManager + Queue + Worker | Bare clone lifecycle, worktree creation, async task processing |
| **Brain** | Ollama or Anthropic | LLM reasoning — local (Qwen 2.5) or cloud (Claude Sonnet) |
| **Agent** | Agentic loop + repetition detection | Observe → Plan → Act → Verify → Iterate, with loop-break safety |
| **Tools** | File, Terminal, Git, GitLab, Search | How the agent reads, edits, and interacts with code |
| **Memory** | Repo memory (persistent) + task log (ephemeral) | What Saturn knows and remembers across tasks |
| **Context** | Repo snapshot + worktree snapshot | Deep codebase awareness for every decision |
| **Safety** | Blocked commands, max loops, path sandboxing, repetition detection | Prevents runaway loops and catastrophic actions |

## 📁 Project Structure

```
saturn/
├── main.py                  # Entry point (server, CLI, or local mode)
├── config.py                # Settings from saturn.env (Pydantic)
├── pyproject.toml           # Dependencies
├── saturn.env               # Your environment variables (git-ignored)
│
├── server/                  # Web API layer
│   ├── app.py               # FastAPI app (repo init on startup)
│   ├── models.py            # Pydantic models (TaskRequest, enums)
│   └── routes/
│       ├── cliq_webhook.py  # POST /webhook/cliq (Zoho Cliq bot)
│       ├── tasks.py         # POST /tasks/submit (direct REST API)
│       └── health.py        # GET /health
│
├── dispatcher/              # Repo + task orchestration
│   ├── workspace.py         # RepoManager (bare clone + worktrees)
│   ├── queue.py             # Async task queue
│   └── worker.py            # Background task processor
│
├── agent/                   # Core agentic loop
│   ├── agent.py             # Main loop (observe→plan→act→verify)
│   ├── brain.py             # Dual LLM: Ollama (local) / Anthropic (cloud)
│   ├── context.py           # Repo + worktree snapshot builder
│   ├── memory.py            # Two-tier: repo memory + task log
│   └── prompts.py           # System prompt + hard-problem addon
│
├── tools/                   # Tool implementations
│   ├── registry.py          # Tool schemas + executor router
│   ├── filesystem.py        # read_file, edit_file, create_file, list_directory
│   ├── terminal.py          # run_command (sandboxed shell execution)
│   ├── git.py               # status, diff, log, commit, push (--force for saturn/)
│   ├── gitlab.py            # create_merge_request via GitLab API
│   └── search.py            # Code search (grep/ripgrep)
│
├── integrations/            # External services
│   ├── cliq.py              # Zoho Cliq bot messaging
│   └── deluge_bot_script.deluge  # Deluge script for Cliq bot
│
├── utils/
│   └── logging.py           # Logging utilities
│
├── tests/                   # Test suite
│   ├── test_agent.py
│   ├── test_tools.py
│   ├── test_terminal.py
│   └── test_webhook.py
│
└── test_server.py           # API endpoint tests
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
cp saturn.env.example saturn.env
```

Edit `saturn.env`:
```env
# ── LLM Provider ── ("ollama" for local, "anthropic" for cloud)
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b

# Or use Anthropic Claude:
# LLM_PROVIDER=anthropic
# ANTHROPIC_API_KEY=sk-ant-...

# ── The repo this Saturn instance watches ──
REPO_URL=https://gitlab.yourcompany.com/group/repo.git
REPO_LOCAL_PATH=/path/to/saturn/repos/myrepo
WORKTREE_BASE_DIR=/path/to/saturn/worktrees

# ── GitLab (for MR creation) ──
GITLAB_URL=https://gitlab.yourcompany.com
GITLAB_TOKEN=glpat-xxxxxxxxxxxxxxxxxxxx
GITLAB_PROJECT_ID=123
GITLAB_DEFAULT_BRANCH=main
```

### 3. Start Ollama (if using local LLM)

```bash
# Install Ollama: https://ollama.com
ollama pull qwen2.5:7b
ollama serve  # runs on localhost:11434
```

### 4. Run the server

```bash
python main.py
```

Saturn will:
1. Clone the repo (first run) or fetch updates
2. Start the FastAPI server on port 8000
3. Start the background worker

### 5. Submit a task

**Via REST API** (easiest for testing):
```bash
curl -X POST http://localhost:8000/tasks/submit \
  -H "Content-Type: application/json" \
  -d '{"description": "Create a hello world script", "task_type": "feature"}'
```

**Via CLI** (one-shot):
```bash
python main.py --task "Fix the failing tests"
```

**Via Zoho Cliq** (production):
Send a message to the configured Cliq channel and the bot picks it up.

### 6. Check health

```bash
curl http://localhost:8000/health
# {"status":"ok","agent":"saturn","version":"0.2.0","repo":"..."}
```

### 7. Run tests

```bash
pytest tests/ -v
```

## 🧠 LLM Providers

Saturn supports two LLM backends:

| Provider | Model | Speed | Cost | Best For |
|----------|-------|-------|------|----------|
| **Ollama** (local) | `qwen2.5:7b` | ~3s/call | Free | Development, simple tasks |
| **Anthropic** (cloud) | `claude-sonnet-4-20250514` | ~2s/call | Pay-per-token | Complex reasoning, large codebases |

Switch in `saturn.env`:
```env
# Local (free)
LLM_PROVIDER=ollama
OLLAMA_MODEL=qwen2.5:7b

# Cloud (powerful)
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

## 📡 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/tasks/submit` | Submit a task directly (JSON body) |
| `POST` | `/webhook/cliq` | Receive task from Zoho Cliq bot |
| `GET`  | `/health` | Health check + version + repo info |

### POST /tasks/submit

```json
{
  "description": "Add input validation to the user signup endpoint",
  "task_type": "feature",
  "priority": "medium"
}
```

Response:
```json
{
  "status": "queued",
  "task_id": "SATURN-A1B2C3D4",
  "description": "Add input validation to the user signup endpoint",
  "task_type": "feature",
  "priority": "medium",
  "queue_size": 1
}
```

## 🦊 GitLab Setup

### Access Token

1. Go to your GitLab project → **Settings** → **Access Tokens**
2. Create a **Project Access Token** with scopes:
   - `api` — create MRs, read issues
   - `read_repository` — clone/fetch
   - `write_repository` — push branches
3. Copy the token → put in `saturn.env` as `GITLAB_TOKEN`

### Finding Project ID

`GITLAB_PROJECT_ID` can be either:
- **Numeric ID**: Found on the project's main page under the name
- **Path**: `your-group/your-repo`

### Push Behavior

Saturn uses `git push --force` for `saturn/` branches because:
- Worktrees are created fresh from `origin/main` via `git worktree add -B`
- The local branch has no remote tracking ref in a bare clone
- `saturn/` branches are exclusively owned by the bot — force-push is safe

## 🔗 Zoho Cliq Setup

Saturn uses the **Zoho Cliq Threads API v2** to group all task updates in a single thread — keeping your channel clean.

### How It Works

```
User sends: "Fix the login timeout bug"
           │
           ▼
┌─────────────────────────────────────────────────┐
│  🧵 Thread: "🪐 Saturn — SATURN-A1B2C3D4: Fix..." │
│                                                    │
│  🤖 Task Received                                  │  ← Thread created
│  📋 Description: Fix the login timeout bug         │
│  🏷️ Type: bug_fix | Priority: medium               │
│  ⏳ Working on it...                               │
│                                                    │
│  📡 Fetching latest from origin...                  │  ← Progress reply
│  🌿 Creating isolated worktree...                   │  ← Progress reply
│  🧠 Agent started — reasoning about the task...     │  ← Progress reply
│                                                    │
│  ✅ Task Complete — SATURN-A1B2C3D4                 │  ← Final reply
│  📝 Summary: Increased session TTL...               │
│  🔗 MR: https://gitlab.company.com/.../mr/42       │
│  📁 Files Changed: auth.config.ts                   │
│  ⏱️ 45s | 🔁 8 iterations                          │
└─────────────────────────────────────────────────┘
```

### Configuration

1. Go to **Zoho Cliq** → **Bots** → **Create Bot**
2. Set the bot's **Webhook URL** to: `https://your-server.com/webhook/cliq`
3. Add these to `saturn.env`:

```env
# Bot API URL (from Cliq bot settings)
CLIQ_BOT_API_URL=https://cliq.zoho.in/company/XXXXX/api/v2/channelsbyname/yourchannel/message

# OAuth token (from Zoho API Console)
CLIQ_AUTH_TOKEN=1000.xxxxxxxxxxxx.xxxxxxxxxxxx

# Channel unique name (for Threads API — found in channel settings)
CLIQ_CHANNEL_UNIQUE_NAME=yourchannel
```

### Threads API Reference

Saturn uses these Cliq API v2 endpoints:

| Action | API | When |
|--------|-----|------|
| **Create thread** | `POST /channelsbyname/{channel}/threads` | Task received |
| **Reply to thread** | `POST /channelsbyname/{channel}/threads/{id}/messages` | Progress updates, completion |
| **Channel message** | `POST /channelsbyname/{channel}/message` | Fallback if thread creation fails |

See: https://www.zoho.com/cliq/help/restapi/v2/#Threads

### Example Cliq messages:

```
Fix the login timeout bug
Add rate limiting to all API routes
Refactor the auth module to use async/await
Write tests for the utils.py functions
Create a hello world program
```

## 🌿 Git Worktrees — Why?

| Full Clone (old) | Git Worktree (Saturn) |
|---|---|
| Clone entire repo per task (~30s) | `git worktree add` (~0.5s) |
| No shared history | Shared bare clone = full history |
| Deleted after task | Bare clone persists forever |
| One task at a time | Multiple worktrees in parallel |
| No memory across tasks | Persistent repo memory |

## 🔄 Repetition Detection

Small local LLMs (like Qwen 2.5 7B) sometimes get stuck calling the same tool repeatedly. Saturn handles this:

1. **Detection**: Tracks tool call signatures — if the same call happens 3x in a row, it's a loop
2. **Nudge**: Sends a context-aware message telling the LLM to move on
   - For `create_file`/`edit_file` loops → "The file is done, stop"
   - For `list_directory` loops → "Move to the next step, use create_file"
3. **Force-break**: After 2 nudges (= 6+ identical calls), the loop is force-terminated
4. **Auto-finalize**: Even after a force-break, Saturn still commits, pushes, and creates the MR

## 🧠 Persistent Memory

Saturn remembers across tasks:

```json
{
  "past_tasks": [
    {
      "task_id": "saturn/fix/login-timeout",
      "date": "2026-03-08",
      "description": "Fix the login timeout bug",
      "summary": "Increased session TTL from 30m to 24h in auth.config.ts",
      "pr_url": "https://gitlab.company.com/group/repo/-/merge_requests/42"
    }
  ],
  "knowledge": {
    "test_framework": {"value": "jest with React Testing Library"},
    "auth_module": {"value": "Uses Devise with JWT sessions"}
  }
}
```

## 🔒 Safety

- **Blocked commands**: `rm -rf /`, `DROP TABLE`, fork bombs, etc.
- **Max loop limit**: 20 iterations (configurable via `MAX_LOOP_ITERATIONS`)
- **Repetition detection**: Force-breaks LLM tool loops after 2 nudges
- **Worktree isolation**: Each task runs in its own directory
- **Path sandboxing**: Tools cannot access files outside the worktree
- **Dry-run mode**: Preview changes without writing (`--dry-run`)
- **Auto-verify**: Tests run after every edit — self-heals failures
- **Force-push safety**: Only `saturn/` prefixed branches are force-pushed
- **Stale cleanup**: Prunes orphan worktrees from crashed runs on startup

## 🛠 Development

```bash
# Install in dev mode
pip install -e .

# Run tests
pytest tests/ -v

# Run the test server script
python test_server.py

# Start server
python main.py
```

## 📜 License

MIT

---

Built with 🧠 Ollama/Claude + 🪐 Saturn
