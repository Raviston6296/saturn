"""
Direct task submission endpoint — for testing without Zoho Cliq.

POST /tasks/submit  →  submit a plain-text task directly
GET  /tasks/status   →  check queue size / worker status
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from config import settings
from server.models import TaskRequest, TaskType, TaskPriority
from server.routes.cliq_webhook import _generate_branch_name
from dispatcher.queue import task_queue

router = APIRouter(prefix="/tasks", tags=["tasks"])


class DirectTaskPayload(BaseModel):
    """Simple task submission payload for testing."""
    description: str
    task_type: str = "unknown"
    priority: str = "medium"
    branch_name: str = ""


@router.post("/submit")
async def submit_task(payload: DirectTaskPayload):
    """
    Submit a task directly (no Cliq involved).
    Great for local testing and development.
    """
    # Match enum values
    task_type = TaskType.UNKNOWN
    for tt in TaskType:
        if tt.value == payload.task_type:
            task_type = tt
            break

    priority = TaskPriority.MEDIUM
    for tp in TaskPriority:
        if tp.value == payload.priority:
            priority = tp
            break

    # Generate a branch name if not provided
    branch_name = payload.branch_name or _generate_branch_name(task_type, payload.description)

    task = TaskRequest(
        raw_message=payload.description,
        description=payload.description,
        task_type=task_type,
        priority=priority,
        branch_name=branch_name,
        sender="test-user",
        channel_id=settings.cliq_channel_unique_name,
    )

    await task_queue.put(task)

    return {
        "status": "queued",
        "task_id": task.id,
        "description": task.description[:120],
        "task_type": task.task_type.value,
        "priority": task.priority.value,
        "queue_size": task_queue.qsize(),
    }


@router.get("/status")
async def task_status():
    """Check the current queue status."""
    return {
        "queue_size": task_queue.qsize(),
        "queue_maxsize": task_queue.maxsize,
        "queue_empty": task_queue.empty(),
    }


class GateTestPayload(BaseModel):
    """Payload for direct gate testing (no LLM)."""
    suite: str = "trim"  # Suite shortcut (trim, join, csv, etc.)
    skip_compile: bool = False  # Skip compilation, use existing jars
    workspace: str = ""  # Optional: specific workspace path
    only_unit_tests: bool = False  # Skip compile and build-test-jar, run only unit tests


@router.post("/test-gates")
async def test_gates(payload: GateTestPayload):
    """
    Run gates directly WITHOUT LLM — for testing gates configuration.

    Examples:
        # Run full gates (compile + build-test + unit-tests) for ZDTrimSuite
        curl -X POST http://localhost:8000/tasks/test-gates \\
            -H "Content-Type: application/json" \\
            -d '{"suite": "trim"}'

        # Run only unit tests (skip compile, faster)
        curl -X POST http://localhost:8000/tasks/test-gates \\
            -H "Content-Type: application/json" \\
            -d '{"suite": "trim", "only_unit_tests": true}'
    """
    # Get workspace path
    workspace = None
    created_temp_worktree = False
    temp_worktree_name = None

    if payload.workspace:
        workspace = Path(payload.workspace)
        if not workspace.exists():
            return {"status": "error", "error": f"Workspace not found: {payload.workspace}"}
    else:
        # Find a zdpas worktree or repo
        worktree_base = Path(settings.worktree_base_dir)
        repo_path = Path(settings.repo_local_path)

        # Check worktrees directory
        if worktree_base.exists():
            for d in sorted(worktree_base.iterdir(), reverse=True):  # Most recent first
                if d.is_dir() and (d / "source" / "com" / "zoho" / "dpaas").exists():
                    workspace = d
                    break

        # Check repo path (might be regular checkout, not bare)
        if not workspace and repo_path.exists():
            if (repo_path / "source" / "com" / "zoho" / "dpaas").exists():
                workspace = repo_path

        # If still no workspace, create a temporary worktree from bare repo
        if not workspace and repo_path.exists():
            # Check if it's a bare repo
            is_bare = (repo_path / "HEAD").exists() and not (repo_path / ".git").exists()

            if is_bare:
                # Create a temporary worktree for testing
                temp_worktree_name = "SATURN-GATE-TEST"
                temp_worktree_path = worktree_base / temp_worktree_name

                # Remove old test worktree if exists
                if temp_worktree_path.exists():
                    subprocess.run(
                        f"git -C {repo_path} worktree remove --force {temp_worktree_path}",
                        shell=True, capture_output=True
                    )

                # Create new worktree
                print(f"📂 Creating temporary worktree for gate testing...")
                print(f"   Repo path: {repo_path}")
                print(f"   Worktree path: {temp_worktree_path}")
                print(f"   Branch: {settings.gitlab_default_branch}")
                result = subprocess.run(
                    f"git -C {repo_path} worktree add --detach {temp_worktree_path} {settings.gitlab_default_branch}",
                    shell=True, capture_output=True, text=True
                )

                if result.returncode == 0 and temp_worktree_path.exists():
                    workspace = temp_worktree_path
                    created_temp_worktree = True
                    print(f"✅ Created worktree: {workspace}")
                else:
                    return {
                        "status": "error",
                        "error": "Could not create worktree from bare repo",
                        "details": result.stderr,
                        "repo_path": str(repo_path),
                    }

    if not workspace or not workspace.exists():
        return {
            "status": "error",
            "error": "No zdpas workspace found and could not create one",
            "hint": "Provide 'workspace' parameter with path to zdpas checkout",
            "worktree_base": str(settings.worktree_base_dir),
            "repo_path": str(settings.repo_local_path),
        }

    # Set environment
    env = os.environ.copy()

    # Use the shared resolver — same logic as gates/executor.py.
    # Checks os.environ first (populated by load_dotenv + DpaasInitializer),
    # then falls back to explicit SATURN_DPAAS_HOME / SATURN_BUILD_FILE_HOME.
    from gates import resolve_dpaas_env
    dpaas_home, build_file_home = resolve_dpaas_env()

    if dpaas_home:
        env["DPAAS_HOME"] = dpaas_home
    else:
        env.pop("DPAAS_HOME", None)

    if build_file_home:
        env["BUILD_FILE_HOME"] = build_file_home
    else:
        env.pop("BUILD_FILE_HOME", None)

    env["SATURN_TEST_MODULES"] = payload.suite

    if payload.skip_compile or payload.only_unit_tests:
        env["SKIP_COMPILE"] = "true"

    # Build command
    saturn_home = Path(__file__).parent.parent.parent
    validate_script = saturn_home / "validate_gates.sh"

    if not validate_script.exists():
        return {
            "status": "error",
            "error": f"validate_gates.sh not found at {validate_script}",
        }

    print(f"🧪 Testing gates: suite={payload.suite}, workspace={workspace}")
    print(f"   DPAAS_HOME={env.get('DPAAS_HOME', '(not set)')}")
    print(f"   SATURN_TEST_MODULES={payload.suite}")
    if payload.only_unit_tests:
        print(f"   Mode: only_unit_tests (skipping compile)")

    try:
        result = subprocess.run(
            ["bash", str(validate_script), str(workspace), payload.suite],
            env=env,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=600,
        )

        success = result.returncode == 0
        status_icon = "✅" if success else "❌"
        print(f"{status_icon} Gates {'passed' if success else 'failed'} (exit={result.returncode})")

        # Extract key info from output
        output_lines = result.stdout.split('\n') if result.stdout else []
        test_summary = [l for l in output_lines if 'test' in l.lower() or 'suite' in l.lower() or '✅' in l or '❌' in l]

        response = {
            "status": "passed" if success else "failed",
            "exit_code": result.returncode,
            "suite": payload.suite,
            "workspace": str(workspace),
            "summary": test_summary[-10:] if test_summary else [],
            "stdout": result.stdout[-3000:] if result.stdout else "",
            "stderr": result.stderr[-500:] if result.stderr else "",
        }

        # Cleanup temporary worktree
        if created_temp_worktree and temp_worktree_name:
            print(f"🧹 Cleaning up temporary worktree...")
            subprocess.run(
                f"git -C {repo_path} worktree remove --force {workspace}",
                shell=True, capture_output=True
            )

        return response

    except subprocess.TimeoutExpired:
        # Cleanup on timeout
        if created_temp_worktree and temp_worktree_name:
            subprocess.run(
                f"git -C {repo_path} worktree remove --force {workspace}",
                shell=True, capture_output=True
            )
        return {"status": "timeout", "error": "Gate execution timed out (600s)", "suite": payload.suite}
    except Exception as e:
        # Cleanup on error
        if created_temp_worktree and temp_worktree_name:
            subprocess.run(
                f"git -C {repo_path} worktree remove --force {workspace}",
                shell=True, capture_output=True
            )
        return {"status": "error", "error": str(e), "suite": payload.suite}



