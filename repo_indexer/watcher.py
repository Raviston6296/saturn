"""
File-system watcher — monitors the repo for changes and re-indexes automatically.

Uses watchdog with debouncing so rapid saves don't hammer the index.

Public API
----------
start_watcher(repo_path, collection)  → Observer
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from rich.console import Console

from repo_indexer.config import SUPPORTED_EXTENSIONS, IGNORE_DIRS, DEBOUNCE_SECONDS
from repo_indexer.indexer import index_file, delete_file_from_index

_console = Console()


class RepoChangeHandler(FileSystemEventHandler):
    """
    Handles created / modified / deleted / moved events for code files.

    Debounces rapid changes per file (e.g. editor auto-save) so the same
    file isn't re-indexed more than once within DEBOUNCE_SECONDS.
    """

    def __init__(self, collection, repo_path: str):
        super().__init__()
        self.collection = collection
        self.repo_path = repo_path
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    # ── event routing ──────────────────────────────────────────────

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._debounce_reindex(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._debounce_reindex(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle_delete(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._handle_delete(event.src_path)
            self._debounce_reindex(event.dest_path)

    # ── helpers ────────────────────────────────────────────────────

    def _should_handle(self, filepath: str) -> bool:
        """Return True if the file should be indexed."""
        path = Path(filepath)

        # Skip ignored directories
        for part in path.parts:
            if part in IGNORE_DIRS or part.endswith(".egg-info"):
                return False

        # Check extension
        if path.name.endswith(".env.example"):
            return True
        return path.suffix.lower() in SUPPORTED_EXTENSIONS

    def _debounce_reindex(self, filepath: str) -> None:
        """Schedule a re-index after DEBOUNCE_SECONDS, cancelling any pending timer."""
        if not self._should_handle(filepath):
            return

        with self._lock:
            existing = self._timers.pop(filepath, None)
            if existing:
                existing.cancel()

            timer = threading.Timer(DEBOUNCE_SECONDS, self._do_reindex, args=(filepath,))
            timer.daemon = True
            self._timers[filepath] = timer
            timer.start()

    def _do_reindex(self, filepath: str) -> None:
        """Actually re-index a single file (runs in timer thread)."""
        try:
            rel = Path(filepath).relative_to(self.repo_path)
            n = index_file(filepath, self.collection)
            _console.print(f"  [green]♻ Re-indexed[/] {rel} ({n} chunks)")
        except Exception as e:
            _console.print(f"  [red]⚠ Re-index failed:[/] {filepath} — {e}")
        finally:
            with self._lock:
                self._timers.pop(filepath, None)

    def _handle_delete(self, filepath: str) -> None:
        """Remove a deleted file from the index."""
        if not self._should_handle(filepath):
            return
        try:
            delete_file_from_index(filepath, self.collection)
            rel = Path(filepath).relative_to(self.repo_path)
            _console.print(f"  [yellow]🗑 Removed from index:[/] {rel}")
        except Exception as e:
            _console.print(f"  [red]⚠ Delete failed:[/] {filepath} — {e}")


def start_watcher(repo_path: str, collection) -> Observer:
    """
    Create and start a watchdog Observer monitoring *repo_path*.

    Returns the Observer so the caller can stop it (``observer.stop()``).
    """
    handler = RepoChangeHandler(collection, repo_path)
    observer = Observer()
    observer.schedule(handler, repo_path, recursive=True)
    observer.daemon = True
    observer.start()
    _console.print(f"[bold green]👁 Watching[/] {repo_path} for changes…")
    return observer

