#!/usr/bin/env python3
"""
repo_indexer CLI — semantic code search for any local repo.

Commands
--------
  index   Full-repo indexing with progress bar
  ask     One-shot question → retrieval → Cursor CLI answer
  watch   Live watcher + interactive REPL
  stats   Show index statistics
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import typer
import chromadb
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.markdown import Markdown

from repo_indexer.config import (
    CHROMA_PATH,
    COLLECTION_NAME,
    TOP_K,
    resolve_repo_path,
)
from repo_indexer.indexer import index_repo
from repo_indexer.retriever import search, build_context
from repo_indexer.llm import ask_cursor
from repo_indexer.watcher import start_watcher

app = typer.Typer(
    name="repo-indexer",
    help="🔍 Semantic code search — index, ask, and watch your codebase.",
    add_completion=False,
)
console = Console()


# ── Shared helpers ────────────────────────────────────────────────


def _get_collection(chroma_path: str = CHROMA_PATH):
    """Return a persistent ChromaDB collection (creates if needed)."""
    client = chromadb.PersistentClient(path=chroma_path)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _ensure_indexed(repo: str, chroma_path: str = CHROMA_PATH):
    """Index the repo if the collection is empty."""
    collection = _get_collection(chroma_path)
    if collection.count() == 0:
        console.print("[yellow]Index is empty — running full index first…[/]\n")
        summary = index_repo(repo, collection)
        _print_summary(summary)
        console.print()
    return collection


def _print_summary(summary: dict) -> None:
    table = Table(title="📊 Indexing Summary", show_lines=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold white", justify="right")
    table.add_row("Files indexed", str(summary["files_indexed"]))
    table.add_row("Chunks stored", str(summary["chunks_indexed"]))
    table.add_row("Files skipped", str(summary["skipped"]))
    table.add_row("Duration", f"{summary['duration_seconds']}s")
    console.print(table)


def _print_sources(results: list[dict], repo: str) -> None:
    """Print source references after an answer."""
    if not results:
        return
    console.print("\n[bold cyan]📎 Sources used:[/]")
    seen: set[str] = set()
    for r in results:
        filepath = r["file"]
        if repo and filepath.startswith(repo):
            filepath = filepath[len(repo):].lstrip("/\\")
        key = f"{filepath}:{r['start_line']}"
        if key in seen:
            continue
        seen.add(key)
        score_pct = r["score"] * 100
        console.print(
            f"  • [white]{filepath}[/] "
            f"lines {r['start_line']}–{r['end_line']} "
            f"[dim](score {score_pct:.1f}%)[/]"
        )


# ── CLI Commands ──────────────────────────────────────────────────


@app.command()
def index(
    repo: str = typer.Option(".", "--repo", "-r", help="Path to the repository"),
    chroma_path: str = typer.Option(CHROMA_PATH, "--db", help="ChromaDB storage path"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-index even if already indexed"),
):
    """Index (or re-index) an entire repository."""
    repo_abs = str(resolve_repo_path(repo))
    console.print(Panel(f"[bold]Indexing[/] {repo_abs}", title="🔍 repo-indexer"))


    collection = _get_collection(chroma_path)

    if force and collection.count() > 0:
        console.print("[yellow]Force flag set — clearing existing index…[/]")
        client = chromadb.PersistentClient(path=chroma_path)
        client.delete_collection(COLLECTION_NAME)
        collection = _get_collection(chroma_path)

    summary = index_repo(repo_abs, collection)
    console.print()
    _print_summary(summary)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Natural language question about the codebase"),
    repo: str = typer.Option(".", "--repo", "-r", help="Path to the repository"),
    chroma_path: str = typer.Option(CHROMA_PATH, "--db", help="ChromaDB storage path"),
    top_k: int = typer.Option(TOP_K, "--top-k", "-k", help="Number of chunks to retrieve"),
):
    """Ask a question about the codebase. Retrieves relevant code and answers via Cursor CLI."""
    repo_abs = str(resolve_repo_path(repo))

    collection = _ensure_indexed(repo_abs, chroma_path)

    console.print(f"[bold]🔎 Searching for:[/] {question}\n")

    results = search(question, collection, top_k=top_k)
    if not results:
        console.print("[red]No relevant code found in the index.[/]")
        raise typer.Exit(1)

    context = build_context(results, repo_path=repo_abs)

    console.print("[dim]─" * 60 + "[/]")
    answer = ask_cursor(question, context, repo_path=repo_abs)
    console.print("[dim]─" * 60 + "[/]")

    _print_sources(results, repo_abs)


@app.command()
def watch(
    repo: str = typer.Option(".", "--repo", "-r", help="Path to the repository"),
    chroma_path: str = typer.Option(CHROMA_PATH, "--db", help="ChromaDB storage path"),
    top_k: int = typer.Option(TOP_K, "--top-k", "-k", help="Number of chunks to retrieve"),
):
    """
    Start a file watcher + interactive REPL.

    Files are re-indexed on save. Type questions and get instant answers.
    Press Ctrl+C to exit.
    """
    repo_abs = str(resolve_repo_path(repo))

    collection = _ensure_indexed(repo_abs, chroma_path)

    # Start the file watcher
    observer = start_watcher(repo_abs, collection)

    console.print(
        Panel(
            "[bold green]Ready![/]\n"
            "Type a question and press Enter.\n"
            "Press [bold]Ctrl+C[/] to exit.",
            title="🪐 repo-indexer REPL",
        )
    )

    try:
        while True:
            try:
                question = console.input("[bold cyan]❯ [/]").strip()
            except EOFError:
                break

            if not question:
                continue
            if question.lower() in ("exit", "quit", "q"):
                break

            # Special commands
            if question.lower() == "/reindex":
                console.print("[yellow]Re-indexing…[/]")
                summary = index_repo(repo_abs, collection)
                _print_summary(summary)
                continue

            if question.lower() == "/stats":
                _show_stats(collection, chroma_path)
                continue

            results = search(question, collection, top_k=top_k)
            if not results:
                console.print("[red]No relevant code found.[/]\n")
                continue

            context = build_context(results, repo_path=repo_abs)

            console.print("[dim]─" * 60 + "[/]")
            ask_cursor(question, context, repo_path=repo_abs)
            console.print("[dim]─" * 60 + "[/]")

            _print_sources(results, repo_abs)
            console.print()

    except KeyboardInterrupt:
        console.print("\n[bold yellow]Shutting down…[/]")
    finally:
        observer.stop()
        observer.join(timeout=3)
        console.print("[dim]Watcher stopped.[/]")


@app.command()
def stats(
    chroma_path: str = typer.Option(CHROMA_PATH, "--db", help="ChromaDB storage path"),
):
    """Show index statistics."""
    collection = _get_collection(chroma_path)
    _show_stats(collection, chroma_path)


def _show_stats(collection, chroma_path: str) -> None:
    """Render index stats as a Rich table."""
    count = collection.count()
    db_path = Path(chroma_path).resolve()
    db_size = sum(f.stat().st_size for f in db_path.rglob("*") if f.is_file()) if db_path.exists() else 0
    db_size_mb = round(db_size / (1024 * 1024), 2)

    # Gather unique files from metadata
    unique_files: set[str] = set()
    if count > 0:
        try:
            # Cap at 10k to avoid OOM on huge repos
            fetch_limit = min(count, 10_000)
            sample = collection.get(limit=fetch_limit, include=["metadatas"])
            for m in sample.get("metadatas") or []:
                if m and "file" in m:
                    unique_files.add(m["file"])
        except Exception:
            pass

    table = Table(title="📊 Index Statistics", show_lines=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="bold white", justify="right")
    table.add_row("Total chunks", str(count))
    table.add_row("Unique files", str(len(unique_files)))
    table.add_row("DB path", str(db_path))
    table.add_row("DB size", f"{db_size_mb} MB")
    console.print(table)


# ── Entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    app()

