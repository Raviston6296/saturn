"""
Direct task submission endpoint — for testing without Zoho Cliq.

POST /tasks/submit  →  submit a plain-text task directly
GET  /tasks/status   →  check queue size / worker status
"""

from __future__ import annotations


from fastapi import APIRouter
from pydantic import BaseModel

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
        channel_id="test-channel",
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
    import os
    import subprocess
    from pathlib import Path
    from config import settings

    # Get workspace path
    workspace = None

    if payload.workspace:
        workspace = Path(payload.workspace)
        if not workspace.exists():
            return {"status": "error", "error": f"Workspace not found: {payload.workspace}"}
    else:
        # Find a zdpas worktree or repo
        worktree_base = Path(settings.worktree_base_dir)
        repo_path = Path(settings.repo_local_path)

        # Check worktrees first
        if worktree_base.exists():
            for d in sorted(worktree_base.iterdir(), reverse=True):  # Most recent first
                if d.is_dir() and (d / "source" / "com" / "zoho" / "dpaas").exists():
                    workspace = d
                    break

        # Check repo path
        if not workspace and repo_path.exists():
            if (repo_path / "source" / "com" / "zoho" / "dpaas").exists():
                workspace = repo_path
            # Check worktrees subdir
            worktrees_in_repo = repo_path / "worktrees"
            if not workspace and worktrees_in_repo.exists():
                for d in sorted(worktrees_in_repo.iterdir(), reverse=True):
                    if d.is_dir() and (d / "source" / "com" / "zoho" / "dpaas").exists():
                        workspace = d
                        break

    if not workspace or not workspace.exists():
        return {
            "status": "error",
            "error": "No zdpas workspace found",
            "hint": "Provide 'workspace' parameter with path to zdpas checkout",
            "worktree_base": str(settings.worktree_base_dir),
            "repo_path": str(settings.repo_local_path),
        }

    # Set environment
    env = os.environ.copy()
    env["DPAAS_HOME"] = str(settings.saturn_dpaas_home)
    env["BUILD_FILE_HOME"] = str(settings.saturn_build_file_home)
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
    print(f"   DPAAS_HOME={env['DPAAS_HOME']}")
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

        return {
            "status": "passed" if success else "failed",
            "exit_code": result.returncode,
            "suite": payload.suite,
            "workspace": str(workspace),
            "summary": test_summary[-10:] if test_summary else [],
            "stdout": result.stdout[-3000:] if result.stdout else "",
            "stderr": result.stderr[-500:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "error": "Gate execution timed out (600s)", "suite": payload.suite}
    except Exception as e:
        return {"status": "error", "error": str(e), "suite": payload.suite}



