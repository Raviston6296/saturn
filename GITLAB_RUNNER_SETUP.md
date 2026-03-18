# 🏃 GitLab Runner Configuration for Saturn

This guide explains how to set up a **dedicated GitLab Runner for Saturn** that operates
**in parallel** with your existing CI/CD infrastructure.

---

## 📊 Architecture: Two Runners, Two Purposes

```
                              GitLab Server
                                   │
                                   │
           ┌───────────────────────┴───────────────────────┐
           │                                               │
           ▼                                               ▼
┌─────────────────────────┐                 ┌─────────────────────────┐
│   CI/CD Runner          │                 │   Saturn Runner          │
│   (Existing)            │                 │   (New — Dedicated)      │
├─────────────────────────┤                 ├─────────────────────────┤
│ Tags: [zdpas, shell]    │                 │ Tags: [saturn, shell]    │
│                         │                 │                          │
│ Purpose:                │                 │ Purpose:                 │
│ • Every commit/MR       │                 │ • Autonomous coding      │
│ • Full test suite       │                 │ • Task processing        │
│ • Build artifacts       │                 │ • Fast validation        │
│ • 3868 tests (~10 min)  │                 │ • Targeted tests (~2 min)│
│                         │                 │                          │
│ Trigger:                │                 │ Trigger:                 │
│ • git push              │                 │ • Scheduled pipeline     │
│ • MR creation           │                 │ • Manual variable        │
│                         │                 │ • SATURN_WORKER=true     │
└─────────────────────────┘                 └─────────────────────────┘
           │                                               │
           ▼                                               ▼
    Validates ALL code                           Processes Cliq tasks
    before merge                                 Creates MRs for review
```

---

## 🔑 Key Differences

| Aspect | CI/CD Runner | Saturn Runner |
|--------|--------------|---------------|
| **Purpose** | Validate every commit | Process autonomous tasks |
| **Trigger** | Every push/MR | Scheduled or on-demand |
| **Tests** | Full suite (3868 tests) | Targeted (~50-250 tests) |
| **Duration** | ~10 minutes | ~30s - 2 minutes |
| **Output** | Pass/Fail pipeline | Creates MRs |
| **Tags** | `zdpas`, `shell` | `saturn`, `shell` |
| **Persistence** | Ephemeral jobs | Long-running process |

---

## 📋 Prerequisites

Before registering the Saturn runner:

1. **Dedicated VM** — Separate from CI/CD runners (recommended)
2. **GitLab Admin Access** — To get registration token
3. **Saturn Installed** — Following VM_SETUP.md
4. **Cursor CLI** — Installed on the runner machine

---

## 🚀 Step 1: Install GitLab Runner

```bash
# On Ubuntu/Debian
curl -L "https://packages.gitlab.com/install/repositories/runner/gitlab-runner/script.deb.sh" | sudo bash
sudo apt-get install gitlab-runner

# Verify installation
gitlab-runner --version
```

---

## 🔧 Step 2: Register the Saturn Runner

### 2.1 Get Registration Token

1. Go to your GitLab project → **Settings** → **CI/CD** → **Runners**
2. Expand "Runners" section
3. Copy the **registration token**

### 2.2 Register with Saturn-Specific Tags

```bash
sudo gitlab-runner register \
  --non-interactive \
  --url "https://gitlab.yourcompany.com" \
  --registration-token "YOUR_REGISTRATION_TOKEN" \
  --executor "shell" \
  --description "Saturn Autonomous Agent Runner" \
  --tag-list "saturn,shell" \
  --run-untagged="false" \
  --locked="true"
```

**Explanation of flags:**
- `--executor shell` — Run commands directly on the machine (not Docker)
- `--tag-list "saturn,shell"` — Only pick up jobs with these tags
- `--run-untagged="false"` — Don't run jobs without tags (prevents CI conflicts)
- `--locked="true"` — Lock runner to this project only

### 2.3 Verify Registration

```bash
# Check runner status
sudo gitlab-runner list

# Expected output:
# Runtime platform                                    arch=amd64 os=linux
# saturn-runner                                       Executor=shell Token=xxx...
```

---

## ⚙️ Step 3: Configure Runner Environment

### 3.1 Edit Runner Config

```bash
sudo nano /etc/gitlab-runner/config.toml
```

Add/modify the Saturn runner section:

```toml
[[runners]]
  name = "Saturn Autonomous Agent Runner"
  url = "https://gitlab.yourcompany.com"
  token = "YOUR_RUNNER_TOKEN"
  executor = "shell"
  
  [runners.custom_build_dir]
    enabled = true
  
  [runners.cache]
    Type = "local"
    Path = "/data/saturn/cache"
    Shared = true
  
  # Environment variables for Saturn
  environment = [
    "SATURN_HOME=/home/saturn/saturn",
    "DPAAS_HOME=/opt/dpaas",
    "PATH=/home/saturn/saturn/.venv/bin:/home/saturn/.local/bin:/usr/local/bin:/usr/bin:/bin"
  ]
```

### 3.2 Set Up Saturn User for Runner

```bash
# Create saturn user (if not exists)
sudo useradd -m -s /bin/bash saturn

# Add gitlab-runner to saturn group
sudo usermod -aG saturn gitlab-runner

# Create Saturn directories
sudo mkdir -p /data/saturn/{repo,tasks,cache}
sudo mkdir -p /opt/dpaas
sudo chown -R saturn:saturn /data/saturn
sudo chown -R saturn:saturn /opt/dpaas

# Set permissions for gitlab-runner
sudo chmod -R g+rw /data/saturn
```

### 3.3 Install Saturn for the Runner

```bash
# Switch to saturn user
sudo su - saturn

# Clone and install Saturn
cd /home/saturn
git clone https://gitlab.yourcompany.com/your-group/saturn.git
cd saturn

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install Saturn
pip install -e .

# Install Cursor CLI
curl https://cursor.com/install -fsS | bash

# Configure Saturn
cp saturn.env.example saturn.env
nano saturn.env  # Add your configuration
```

---

## 📄 Step 4: Create GitLab CI Job for Saturn

Add this to your project's `.gitlab-ci.yml`:

```yaml
# ═══════════════════════════════════════════════════════════════
# Saturn Worker Job — Runs in parallel with regular CI/CD
# ═══════════════════════════════════════════════════════════════

stages:
  - build
  - test
  - saturn  # New stage for Saturn (parallel to CI)

# ─────────────────────────────────────────────────────────────────
# Existing CI/CD Jobs (unchanged)
# ─────────────────────────────────────────────────────────────────

build_job:
  stage: build
  tags:
    - zdpas    # Uses existing CI runner
    - shell
  script:
    - ant build
  # ... your existing build config

test_job:
  stage: test
  tags:
    - zdpas    # Uses existing CI runner
    - shell
  script:
    - ./run_tests.sh
  # ... your existing test config

# ─────────────────────────────────────────────────────────────────
# Saturn Worker Job (NEW — runs on Saturn runner only)
# ─────────────────────────────────────────────────────────────────

saturn_worker:
  stage: saturn
  tags:
    - saturn   # Uses Saturn-specific runner
    - shell
  
  # Only run when explicitly triggered
  rules:
    - if: '$SATURN_WORKER == "true"'
      when: always
    - when: never
  
  # Long timeout for persistent worker
  timeout: 12h
  
  # Don't clone — Saturn manages its own repo
  variables:
    GIT_STRATEGY: none
    SATURN_HOME: "/home/saturn/saturn"
    DPAAS_HOME: "/opt/dpaas"
    BUILD_FILE_HOME: "/home/test/git-runner/ref"
  
  before_script:
    - echo "🪐 Saturn Worker starting on $(hostname)"
    - source /home/saturn/saturn/.venv/bin/activate
    - cd /home/saturn/saturn
  
  script:
    - python main.py
  
  after_script:
    - echo "🪐 Saturn Worker stopped at $(date)"
```

---

## 📅 Step 5: Set Up Scheduled Pipeline

To keep Saturn running continuously, create a scheduled pipeline:

### 5.1 Via GitLab UI

1. Go to **CI/CD** → **Schedules** → **New Schedule**
2. Configure:
   - **Description**: `Saturn Worker`
   - **Interval Pattern**: `*/5 * * * *` (every 5 minutes)
   - **Cron Timezone**: Your timezone
   - **Target Branch**: `master` or `main`
   - **Variables**:
     - Key: `SATURN_WORKER`
     - Value: `true`
3. Click **Save pipeline schedule**

### 5.2 Via GitLab API

```bash
curl --request POST \
  --header "PRIVATE-TOKEN: YOUR_GITLAB_TOKEN" \
  --form "description=Saturn Worker" \
  --form "ref=master" \
  --form "cron=*/5 * * * *" \
  --form "cron_timezone=Asia/Kolkata" \
  --form "active=true" \
  --form "variables[SATURN_WORKER]=true" \
  "https://gitlab.yourcompany.com/api/v4/projects/YOUR_PROJECT_ID/pipeline_schedules"
```

---

## 🔄 How It Works Together

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           GITLAB PROJECT                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  Developer pushes code          User posts task in Cliq                      │
│         │                              │                                      │
│         ▼                              ▼                                      │
│  ┌─────────────────┐          ┌─────────────────┐                            │
│  │ CI/CD Pipeline  │          │ Scheduled       │                            │
│  │ (auto-trigger)  │          │ Pipeline        │                            │
│  │                 │          │ SATURN_WORKER=  │                            │
│  │ tags: zdpas     │          │ true            │                            │
│  └────────┬────────┘          └────────┬────────┘                            │
│           │                            │                                      │
│           ▼                            ▼                                      │
│  ┌─────────────────┐          ┌─────────────────┐                            │
│  │ CI Runner       │          │ Saturn Runner   │                            │
│  │ (existing)      │          │ (new)           │                            │
│  │                 │          │                 │                            │
│  │ • Build code    │          │ • Process task  │                            │
│  │ • Run ALL tests │          │ • Edit code     │                            │
│  │ • Check quality │          │ • Run SOME tests│                            │
│  └────────┬────────┘          │ • Create MR     │                            │
│           │                   └────────┬────────┘                            │
│           │                            │                                      │
│           ▼                            ▼                                      │
│    Pipeline passes?           MR Created by Saturn                           │
│    ✅ Ready for review              │                                        │
│                                      ▼                                        │
│                              CI/CD Pipeline runs                              │
│                              on Saturn's MR                                   │
│                                      │                                        │
│                                      ▼                                        │
│                              Full validation                                  │
│                              before merge                                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## ✅ Step 6: Verify the Setup

### 6.1 Check Runner Status in GitLab

1. Go to **Settings** → **CI/CD** → **Runners**
2. You should see two runners:
   - ✅ `CI Runner` — tags: `zdpas, shell`
   - ✅ `Saturn Runner` — tags: `saturn, shell`

### 6.2 Manually Trigger Saturn Job

```bash
# Via GitLab API
curl --request POST \
  --header "PRIVATE-TOKEN: YOUR_GITLAB_TOKEN" \
  --form "variables[SATURN_WORKER]=true" \
  "https://gitlab.yourcompany.com/api/v4/projects/YOUR_PROJECT_ID/pipeline"
```

### 6.3 Check Saturn Logs

```bash
# On the Saturn runner machine
sudo journalctl -u gitlab-runner -f

# Or view job logs in GitLab UI:
# CI/CD → Jobs → saturn_worker
```

---

## 🛡️ Security Considerations

### Runner Isolation

```yaml
# The Saturn runner should NOT run regular CI jobs
# Enforce this with tags:
saturn_worker:
  tags:
    - saturn   # ONLY runs on saturn-tagged runner
    - shell
```

### Token Security

```bash
# Store GitLab token securely
# Use CI/CD Variables (masked):
# Settings → CI/CD → Variables → Add Variable
# Key: GITLAB_TOKEN
# Value: glpat-xxx
# Flags: ☑ Mask variable
```

### Network Isolation (Optional)

If Saturn should only access specific resources:

```bash
# Firewall rules on Saturn runner
sudo ufw allow from GITLAB_IP to any port 443
sudo ufw allow from CLIQ_IP to any port 443
sudo ufw default deny outgoing
```

---

## 🔧 Troubleshooting

### Runner Not Picking Up Jobs

```bash
# Check runner status
sudo gitlab-runner status

# Verify tags match
sudo gitlab-runner list
# Should show: tags=[saturn, shell]

# Restart runner
sudo gitlab-runner restart
```

### Job Stuck in "Pending"

```bash
# Check if runner is online in GitLab UI
# Settings → CI/CD → Runners

# Check runner logs
sudo journalctl -u gitlab-runner --since "10 minutes ago"
```

### Saturn Process Crashes

```bash
# Check Saturn logs
tail -f /home/saturn/saturn/saturn.log

# Restart via scheduled pipeline
# The next scheduled run (every 5 min) will restart Saturn
```

---

## 📊 Monitoring

### GitLab Pipeline View

- CI/CD → Pipelines → Filter by `saturn_worker`
- Check job duration, status, logs

### Saturn Health Endpoint

```bash
# From the runner machine
curl http://localhost:8000/health

# From external (if exposed)
curl http://saturn-runner.yourcompany.com:8000/health
```

### Metrics to Track

| Metric | Where | Alert Threshold |
|--------|-------|-----------------|
| Runner online | GitLab Runners page | Offline > 5 min |
| Job duration | CI/CD Jobs | > 12 hours |
| Tasks completed | Saturn /health | Queue growing |
| MRs created | GitLab MRs | None in 24h |

---

## 📝 Summary

| Step | Action | Verification |
|------|--------|--------------|
| 1 | Install GitLab Runner | `gitlab-runner --version` |
| 2 | Register with `saturn` tag | Runner appears in GitLab UI |
| 3 | Configure environment | Saturn imports work |
| 4 | Add `saturn_worker` job to CI | Job visible in pipeline |
| 5 | Create scheduled pipeline | Schedule active in UI |
| 6 | Verify end-to-end | Task → MR created |

---

*Saturn Runner — Autonomous coding, parallel to your CI/CD.*

