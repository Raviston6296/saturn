"""
Indexer — walks a repo, chunks files, embeds via Ollama, stores in ChromaDB.

Uses Ollama's built-in embedding endpoint instead of sentence-transformers,
eliminating the ~2 GB PyTorch dependency entirely.

Public API
----------
index_repo(repo_path, collection)       → summary dict
index_file(filepath, collection)        → int (chunks indexed)
delete_file_from_index(filepath, collection)
chunk_file(filepath)                    → list[dict]
embed_texts(texts)                      → list[list[float]]
embed_query(text)                       → list[float]
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import ollama as _ollama
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    MofNCompleteColumn,
    TimeElapsedColumn,
)

from repo_indexer.config import (
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    SUPPORTED_EXTENSIONS,
    IGNORE_DIRS,
    EMBED_MODEL,
    OLLAMA_BASE_URL,
)

# ── Ollama client (lazy singleton) ────────────────────────────────
_client: _ollama.Client | None = None


def _get_client() -> _ollama.Client:
    global _client
    if _client is None:
        _client = _ollama.Client(host=OLLAMA_BASE_URL)
    return _client


# ── Embedding via Ollama ──────────────────────────────────────────


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Embed a list of texts using Ollama's /api/embed endpoint.

    Batches automatically — Ollama handles the list natively.
    """
    client = _get_client()
    response = client.embed(model=EMBED_MODEL, input=texts)

    # ollama SDK >=0.4 returns typed objects; handle both styles
    try:
        embeddings = response.embeddings
    except AttributeError:
        embeddings = response.get("embeddings", response.get("embedding", []))

    # Ollama returns list[list[float]] for multiple inputs
    if embeddings and isinstance(embeddings[0], (int, float)):
        # Single embedding returned as flat list — wrap it
        embeddings = [embeddings]

    return embeddings


def embed_query(text: str) -> list[float]:
    """Embed a single query string."""
    return embed_texts([text])[0]


# ── Chunking ──────────────────────────────────────────────────────


def chunk_file(filepath: str) -> list[dict[str, Any]]:
    """
    Read *filepath*, split into overlapping chunks of CHUNK_SIZE lines.

    Returns
    -------
    list of dicts with keys: text, start_line, end_line, filepath
    """
    path = Path(filepath)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []

    lines = text.splitlines(keepends=True)
    if not lines:
        return []

    chunks: list[dict[str, Any]] = []
    step = max(CHUNK_SIZE - CHUNK_OVERLAP, 1)

    for start in range(0, len(lines), step):
        end = min(start + CHUNK_SIZE, len(lines))
        chunk_text = "".join(lines[start:end])

        # skip nearly-empty chunks
        if len(chunk_text.strip()) < 10:
            continue

        chunks.append({
            "text": chunk_text,
            "start_line": start + 1,      # 1-based
            "end_line": end,               # inclusive
            "filepath": str(path),
        })

        if end >= len(lines):
            break

    return chunks


# ── Language detection (simple, extension-based) ──────────────────

_LANG_MAP: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript", ".java": "java",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".md": "markdown",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".html": "html", ".css": "css", ".scss": "scss",
    ".sh": "shell", ".bash": "shell", ".sql": "sql",
    ".toml": "toml", ".cfg": "ini", ".ini": "ini",
}


def _detect_language(filepath: str) -> str:
    ext = Path(filepath).suffix.lower()
    return _LANG_MAP.get(ext, "text")


# ── Single file indexing ──────────────────────────────────────────


def index_file(filepath: str, collection) -> int:
    """
    Chunk and embed a single file, upsert into ChromaDB.

    Returns the number of chunks indexed.
    """
    # First remove any stale chunks for this file
    delete_file_from_index(filepath, collection)

    chunks = chunk_file(filepath)
    if not chunks:
        return 0

    texts = [c["text"] for c in chunks]
    embeddings = embed_texts(texts)

    language = _detect_language(filepath)

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []
    embedding_list: list[list[float]] = []

    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        chunk_id = f"{filepath}::chunk{i}"
        ids.append(chunk_id)
        documents.append(chunk["text"])
        metadatas.append({
            "file": filepath,
            "start_line": chunk["start_line"],
            "end_line": chunk["end_line"],
            "language": language,
        })
        embedding_list.append(emb)

    collection.upsert(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embedding_list,
    )

    return len(chunks)


# ── Delete file from index ────────────────────────────────────────


def delete_file_from_index(filepath: str, collection) -> None:
    """Remove all chunks for *filepath* from ChromaDB."""
    try:
        collection.delete(where={"file": filepath})
    except Exception:
        # Collection might be empty or file was never indexed
        pass


# ── Full repo indexing ────────────────────────────────────────────

def _should_index(path: Path) -> bool:
    """Return True if the file should be indexed."""
    # Check ignored directories
    parts = path.parts
    for ignore in IGNORE_DIRS:
        if ignore in parts:
            return False

    # Check extension — special case for .env.example
    if path.name.endswith(".env.example"):
        return True
    if path.suffix.lower() in SUPPORTED_EXTENSIONS:
        return True

    return False


def _collect_files(repo_path: Path) -> list[Path]:
    """Walk repo and collect indexable files."""
    files: list[Path] = []
    for root, dirs, filenames in os.walk(repo_path):
        # Prune ignored directories in-place (os.walk respects this)
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.endswith(".egg-info")]
        for fname in filenames:
            fpath = Path(root) / fname
            if _should_index(fpath):
                files.append(fpath)
    return sorted(files)


def index_repo(repo_path: str, collection) -> dict[str, int]:
    """
    Walk the entire repo, index every supported file.

    Returns
    -------
    dict with keys: files_indexed, chunks_indexed, skipped, duration_seconds
    """
    root = Path(repo_path).resolve()
    files = _collect_files(root)

    total_files = 0
    total_chunks = 0
    skipped = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task("Indexing files…", total=len(files))

        t0 = time.time()
        for fpath in files:
            try:
                rel = fpath.relative_to(root)
                progress.update(task, description=f"[cyan]{rel}")
                n = index_file(str(fpath), collection)
                if n > 0:
                    total_files += 1
                    total_chunks += n
                else:
                    skipped += 1
            except Exception:
                skipped += 1
            progress.advance(task)

    return {
        "files_indexed": total_files,
        "chunks_indexed": total_chunks,
        "skipped": skipped,
        "duration_seconds": round(time.time() - t0, 2),
    }
