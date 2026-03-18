"""
Configuration for the repo indexer / semantic code search system.

All values have sensible defaults and can be overridden via environment
variables prefixed with RI_ (e.g. RI_CHUNK_SIZE=80).
"""

from __future__ import annotations

import os
from pathlib import Path


# ── Paths ──────────────────────────────────────────────────────────
REPO_PATH: str = os.getenv("RI_REPO_PATH", ".")
CHROMA_PATH: str = os.getenv("RI_CHROMA_PATH", "./repo_index")

# ── Embedding via Ollama (still used for ChromaDB vector embeddings) ─
EMBED_MODEL: str = os.getenv("RI_EMBED_MODEL", "qwen2.5:7b")

# ── Cursor CLI (replaces Ollama LLM for Q&A) ─────────────────────
CURSOR_CLI_PATH: str = os.getenv("RI_CURSOR_CLI_PATH", "agent")

# ── Legacy Ollama config (only used if EMBED_MODEL needs Ollama) ──
OLLAMA_BASE_URL: str = os.getenv("RI_OLLAMA_BASE_URL", "http://localhost:11434")

# ── Chunking ───────────────────────────────────────────────────────
CHUNK_SIZE: int = int(os.getenv("RI_CHUNK_SIZE", "60"))        # lines per chunk
CHUNK_OVERLAP: int = int(os.getenv("RI_CHUNK_OVERLAP", "10"))  # overlapping lines

# ── Retrieval ──────────────────────────────────────────────────────
TOP_K: int = int(os.getenv("RI_TOP_K", "8"))                   # chunks per query

# ── File filters ───────────────────────────────────────────────────
SUPPORTED_EXTENSIONS: list[str] = [
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".md", ".json", ".yaml", ".yml",
    ".env.example",
    ".java", ".go", ".rs", ".rb",
    ".html", ".css", ".scss",
    ".sh", ".bash",
    ".toml", ".cfg", ".ini",
    ".sql",
    ".dockerfile", ".tf",
]

IGNORE_DIRS: list[str] = [
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "dist", "build", ".next", ".nuxt",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "egg-info", ".eggs", ".tox",
    "repo_index",                             # don't index our own DB
]

# ── Watcher debounce ──────────────────────────────────────────────
DEBOUNCE_SECONDS: float = float(os.getenv("RI_DEBOUNCE", "0.5"))

# ── ChromaDB collection name (derived from repo path) ─────────────
COLLECTION_NAME: str = "repo_chunks"


def resolve_repo_path(repo: str | None = None) -> Path:
    """Return an absolute Path for the repo, falling back to REPO_PATH."""
    p = Path(repo) if repo else Path(REPO_PATH)
    return p.resolve()

