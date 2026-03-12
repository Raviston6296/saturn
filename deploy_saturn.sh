#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# Saturn Deployment Script for GitLab Runner VM
# ═══════════════════════════════════════════════════════════════════════════
#
# This script installs/updates Saturn on the GitLab Runner VM.
#
# Usage:
#   # Fresh install
#   ./deploy_saturn.sh install
#
#   # Update to latest
#   ./deploy_saturn.sh update
#
#   # Switch branch
#   ./deploy_saturn.sh switch-branch deterministic_gates
#
# ═══════════════════════════════════════════════════════════════════════════

set -e

# ── Configuration ──────────────────────────────────────────────────────────
SATURN_REPO="https://gitlab.zoho.com/your-group/saturn.git"
SATURN_BRANCH="fix-gaps-in-gates-subsystem"                    # Change to your branch: main, deterministic_gates, etc.
SATURN_HOME="/home/gitlab-runner/saturn"
DATA_DIR="/data/saturn"
DPAAS_HOME="/opt/dpaas"

# ── Colors ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ═══════════════════════════════════════════════════════════════════════════
# INSTALL — Fresh installation of Saturn
# ═══════════════════════════════════════════════════════════════════════════
install_saturn() {
    log_info "Installing Saturn..."

    # Create directories
    log_info "Creating directories..."
    sudo mkdir -p "$DATA_DIR/repo" "$DATA_DIR/tasks" "$DPAAS_HOME"
    sudo chown -R gitlab-runner:gitlab-runner "$DATA_DIR" "$DPAAS_HOME"

    # Clone Saturn
    if [ -d "$SATURN_HOME" ]; then
        log_warn "Saturn already exists at $SATURN_HOME"
        log_warn "Use 'update' command instead, or remove it first"
        exit 1
    fi

    log_info "Cloning Saturn from $SATURN_REPO (branch: $SATURN_BRANCH)..."
    git clone -b "$SATURN_BRANCH" "$SATURN_REPO" "$SATURN_HOME"
    cd "$SATURN_HOME"

    # Create virtual environment
    log_info "Creating Python virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate

    # Install Saturn
    log_info "Installing Saturn dependencies..."
    pip install --upgrade pip
    pip install -e .

    # Install Cursor CLI
    log_info "Installing Cursor CLI..."
    curl https://cursor.com/install -fsS | bash || log_warn "Cursor CLI install failed - install manually"

    # Copy env template
    if [ ! -f saturn.env ]; then
        log_info "Creating saturn.env from template..."
        cp saturn.env.example saturn.env
        log_warn "⚠️  Edit saturn.env with your actual values!"
    fi

    log_info "✅ Saturn installed successfully!"
    log_info ""
    log_info "Next steps:"
    log_info "  1. Edit $SATURN_HOME/saturn.env with your GitLab token"
    log_info "  2. Verify: cd $SATURN_HOME && source .venv/bin/activate && python -c 'from config import settings; print(settings.repo_url)'"
    log_info "  3. Trigger pipeline with SATURN_HEALTH_CHECK=true"
}

# ═══════════════════════════════════════════════════════════════════════════
# UPDATE — Pull latest changes
# ═══════════════════════════════════════════════════════════════════════════
update_saturn() {
    log_info "Updating Saturn..."

    if [ ! -d "$SATURN_HOME" ]; then
        log_error "Saturn not found at $SATURN_HOME"
        log_error "Run 'install' first"
        exit 1
    fi

    cd "$SATURN_HOME"

    # Get current branch
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    log_info "Current branch: $CURRENT_BRANCH"

    # Stash any local changes
    if ! git diff --quiet; then
        log_warn "Stashing local changes..."
        git stash
    fi

    # Pull latest
    log_info "Pulling latest changes..."
    git fetch origin
    git pull origin "$CURRENT_BRANCH"

    # Update dependencies
    log_info "Updating dependencies..."
    source .venv/bin/activate
    pip install -e .

    log_info "✅ Saturn updated successfully!"
    log_info "   Branch: $CURRENT_BRANCH"
    log_info "   Commit: $(git rev-parse --short HEAD)"
}

# ═══════════════════════════════════════════════════════════════════════════
# SWITCH-BRANCH — Switch to a different branch
# ═══════════════════════════════════════════════════════════════════════════
switch_branch() {
    TARGET_BRANCH="$1"

    if [ -z "$TARGET_BRANCH" ]; then
        log_error "Usage: ./deploy_saturn.sh switch-branch <branch-name>"
        log_error "Available branches:"
        cd "$SATURN_HOME" && git branch -r | sed 's/origin\//  /'
        exit 1
    fi

    log_info "Switching to branch: $TARGET_BRANCH"

    cd "$SATURN_HOME"

    # Fetch all branches
    git fetch origin

    # Check if branch exists
    if ! git rev-parse --verify "origin/$TARGET_BRANCH" >/dev/null 2>&1; then
        log_error "Branch '$TARGET_BRANCH' not found on origin"
        log_error "Available branches:"
        git branch -r | sed 's/origin\//  /'
        exit 1
    fi

    # Stash local changes
    if ! git diff --quiet; then
        log_warn "Stashing local changes..."
        git stash
    fi

    # Switch branch
    git checkout "$TARGET_BRANCH" || git checkout -b "$TARGET_BRANCH" "origin/$TARGET_BRANCH"
    git pull origin "$TARGET_BRANCH"

    # Update dependencies
    log_info "Updating dependencies..."
    source .venv/bin/activate
    pip install -e .

    log_info "✅ Switched to branch: $TARGET_BRANCH"
    log_info "   Commit: $(git rev-parse --short HEAD)"
}

# ═══════════════════════════════════════════════════════════════════════════
# STATUS — Show current status
# ═══════════════════════════════════════════════════════════════════════════
show_status() {
    log_info "Saturn Status"
    echo ""

    if [ ! -d "$SATURN_HOME" ]; then
        log_error "Saturn not installed at $SATURN_HOME"
        exit 1
    fi

    cd "$SATURN_HOME"

    echo "  Installation: $SATURN_HOME"
    echo "  Branch:       $(git rev-parse --abbrev-ref HEAD)"
    echo "  Commit:       $(git rev-parse --short HEAD)"
    echo "  Last update:  $(git log -1 --format='%ci')"
    echo ""
    echo "  Data dir:     $DATA_DIR"
    echo "  DPaaS home:   $DPAAS_HOME"
    echo ""

    # Check if saturn.env exists and has required values
    if [ -f saturn.env ]; then
        echo "  saturn.env:   ✅ exists"
        source .venv/bin/activate 2>/dev/null
        python -c "
from config import settings
print(f'  REPO_URL:     {settings.repo_url or \"❌ NOT SET\"}')
print(f'  GITLAB_URL:   {settings.gitlab_url or \"❌ NOT SET\"}')
print(f'  PROJECT_ID:   {settings.gitlab_project_id or \"❌ NOT SET\"}')
" 2>/dev/null || echo "  Config:       ❌ Error loading"
    else
        echo "  saturn.env:   ❌ MISSING"
    fi
}

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════
case "$1" in
    install)
        install_saturn
        ;;
    update)
        update_saturn
        ;;
    switch-branch)
        switch_branch "$2"
        ;;
    status)
        show_status
        ;;
    *)
        echo "Saturn Deployment Script"
        echo ""
        echo "Usage: $0 {install|update|switch-branch|status}"
        echo ""
        echo "Commands:"
        echo "  install              Fresh installation of Saturn"
        echo "  update               Pull latest changes from current branch"
        echo "  switch-branch NAME   Switch to a different branch"
        echo "  status               Show current installation status"
        echo ""
        echo "Examples:"
        echo "  $0 install"
        echo "  $0 update"
        echo "  $0 switch-branch deterministic_gates"
        echo "  $0 switch-branch main"
        exit 1
        ;;
esac

