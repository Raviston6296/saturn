#!/usr/bin/env python3
"""test_server.py - Start Saturn server and submit dummy tasks for testing."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

DUMMY_TASKS = [
    {"description": "Add a health check utility function in utils/health.py that returns system metrics", "task_type": "feature", "priority": "medium"},
    {"description": "Fix the login bug where session tokens expire prematurely after 5 minutes", "task_type": "bug_fix", "priority": "high"},
    {"description": "Refactor the database connection pool to use async context managers", "task_type": "refactor", "priority": "low"},
]


def wait_for_server(base_url, timeout=30):
    print(f"Waiting for server at {base_url} ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = Request(f"{base_url}/health")
            resp = urlopen(req, timeout=3)
            data = json.loads(resp.read().decode())
            if data.get("status") == "ok":
                ver = data.get("version", "?")
                print(f"Server is up (version: {ver})")
                return True
        except (URLError, ConnectionRefusedError, OSError):
            pass
        time.sleep(1)
    print(f"Server did not start within {timeout}s")
    return False


def submit_task_direct(base_url, task):
    url = f"{base_url}/tasks/submit"
    payload = json.dumps(task).encode("utf-8")
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"   Failed: {e}")
        return None


def submit_task_cliq(base_url, message, sender="TestUser"):
    url = f"{base_url}/webhook/cliq"
    payload = json.dumps({
        "name": sender,
        "message": message,
        "chat_id": "test-chat-123",
        "channel_name": "saturn-test",
        "sender_id": "test-sender-456",
    }).encode("utf-8")
    req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        resp = urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"   Failed: {e}")
        return None


def check_queue_status(base_url):
    try:
        req = Request(f"{base_url}/tasks/status")
        resp = urlopen(req, timeout=5)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"   Failed to check status: {e}")
        return None


def run_dummy_submissions(base_url):
    print("\n" + "=" * 60)
    print("SUBMITTING DUMMY TASKS")
    print("=" * 60)

    print("\nMethod 1: Direct task submission (POST /tasks/submit)")
    print("-" * 50)
    for i, task in enumerate(DUMMY_TASKS, 1):
        desc = task["description"][:70]
        print(f"\n  [{i}/{len(DUMMY_TASKS)}] Submitting: {desc}...")
        result = submit_task_direct(base_url, task)
        if result:
            tid = result.get("task_id", "?")
            tt = result.get("task_type", "?")
            tp = result.get("priority", "?")
            qs = result.get("queue_size", "?")
            print(f"   Queued as {tid}")
            print(f"      Type: {tt} | Priority: {tp} | Queue: {qs}")
        time.sleep(0.5)

    print("\nMethod 2: Cliq webhook simulation (POST /webhook/cliq)")
    print("-" * 50)
    cliq_messages = [
        "Hey Saturn, can you add input validation to the user registration endpoint?",
        "Fix the broken CSV export - it crashes on empty datasets",
    ]
    for i, msg in enumerate(cliq_messages, 1):
        print(f"\n  [{i}/{len(cliq_messages)}] Sending Cliq message: {msg[:60]}...")
        result = submit_task_cliq(base_url, msg)
        if result:
            text = result.get("text", "")
            if "SATURN-" in text:
                parts = text.split("`")
                task_id = parts[1] if len(parts) > 1 else "?"
                print(f"   Acknowledged: {task_id}")
            else:
                print(f"   Response: {text[:100]}")
        time.sleep(0.5)

    print("\nQueue Status")
    print("-" * 50)
    status = check_queue_status(base_url)
    if status:
        qs = status.get("queue_size", "?")
        ms = status.get("queue_maxsize", "?")
        em = status.get("queue_empty", "?")
        print(f"   Queue size: {qs}")
        print(f"   Max size:   {ms}")
        print(f"   Empty:      {em}")

    print("\n" + "=" * 60)
    print("ALL DUMMY TASKS SUBMITTED")
    print("Watch the server logs to see the worker process them.")
    print("=" * 60)


def start_server_and_test(host, port, server_only=False):
    base_url = f"http://{host}:{port}"
    print()
    print("SATURN TEST RUNNER")
    print(f"Server: {base_url}")
    print()

    print("Starting Saturn server...")
    server_proc = subprocess.Popen(
        [sys.executable, "main.py", "--host", host, "--port", str(port)],
        cwd=str(Path(__file__).parent),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    def _shutdown(signum=None, frame=None):
        print("\nShutting down Saturn server...")
        server_proc.terminate()
        try:
            server_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_proc.kill()
        print("Done.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    if not wait_for_server(base_url, timeout=30):
        print("Could not start server. Check logs above.")
        server_proc.terminate()
        sys.exit(1)

    if not server_only:
        run_dummy_submissions(base_url)

    print("\nServer is running. Press Ctrl+C to stop.\n")
    try:
        server_proc.wait()
    except KeyboardInterrupt:
        _shutdown()


def main():
    parser = argparse.ArgumentParser(description="Saturn Test Runner")
    parser.add_argument("--submit-only", action="store_true",
                        help="Only submit tasks (server must be running)")
    parser.add_argument("--server-only", action="store_true",
                        help="Only start server (no auto-submit)")
    parser.add_argument("--url", type=str, default=None,
                        help="Base URL of the server")
    parser.add_argument("--host", type=str, default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    if args.submit_only:
        base_url = args.url or f"http://{args.host}:{args.port}"
        print(f"Submitting tasks to {base_url} ...")
        if not wait_for_server(base_url, timeout=5):
            print("Server is not running.")
            sys.exit(1)
        run_dummy_submissions(base_url)
    else:
        start_server_and_test(args.host, args.port,
                              server_only=args.server_only)


if __name__ == "__main__":
    main()
