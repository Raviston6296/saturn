"""
Retriever — embeds a query, searches ChromaDB, and builds LLM context.

Public API
----------
search(query, collection, top_k)  → list[dict]
build_context(results)            → str
"""

from __future__ import annotations

from typing import Any

from repo_indexer.config import TOP_K
from repo_indexer.indexer import embed_query


def search(
    query: str,
    collection,
    top_k: int = TOP_K,
) -> list[dict[str, Any]]:
    """
    Embed *query* and retrieve the nearest *top_k* chunks from ChromaDB.

    Returns
    -------
    list of dicts: {text, file, start_line, end_line, score, language}
    """
    query_embedding = embed_query(query)

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    hits: list[dict[str, Any]] = []
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for doc, meta, dist in zip(documents, metadatas, distances):
        hits.append({
            "text": doc,
            "file": meta.get("file", ""),
            "start_line": meta.get("start_line", 0),
            "end_line": meta.get("end_line", 0),
            "language": meta.get("language", "text"),
            "score": round(1.0 - dist, 4),      # ChromaDB distance → similarity
        })

    return hits


def build_context(results: list[dict[str, Any]], repo_path: str = "") -> str:
    """
    Format retrieved chunks into a clean context block for the LLM.

    Each chunk is rendered as:
        ### path/to/file.py (lines 10–70)
        ```python
        <code>
        ```
    """
    if not results:
        return "(no relevant code found)"

    sections: list[str] = []
    for r in results:
        filepath = r["file"]
        # Make path relative if repo_path given
        if repo_path and filepath.startswith(repo_path):
            filepath = filepath[len(repo_path):].lstrip("/\\")

        lang = r.get("language", "")
        header = f"### {filepath} (lines {r['start_line']}–{r['end_line']})"
        code_block = f"```{lang}\n{r['text'].rstrip()}\n```"
        sections.append(f"{header}\n{code_block}")

    return "\n\n---\n\n".join(sections)

