"""Optional FastAPI v2 router and snippet/cache helpers for Ratel UI integration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from aegis_code_graph.micro_extractor import extract_file_micro_graph
from aegis_code_graph.models import GraphConfig, GraphResult, MicroMethodGraph


def get_git_head_sha(repo_path: str | Path) -> str | None:
    """Return the current git HEAD commit SHA if repo_path is a git repository."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or None
    except OSError:
        pass
    return None


def read_code_snippet(
    repo_path: str | Path,
    file_path: str,
    line: int = 1,
    context_lines: int = 20,
) -> dict[str, Any]:
    """Read a bounded source code slice around a target line for the Code inspector tab."""
    root = Path(repo_path).resolve()
    rel_posix = file_path.replace("\\", "/").lstrip("./")
    target = (root / rel_posix).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise FileNotFoundError(f"File not found inside repository: {rel_posix}")

    all_lines = target.read_text(encoding="utf-8", errors="ignore").splitlines()
    total = len(all_lines)
    start = max(1, line - context_lines)
    end = min(total, line + context_lines)
    snippet = "\n".join(all_lines[start - 1 : end])

    return {
        "file_path": rel_posix,
        "start_line": start,
        "end_line": end,
        "total_lines": total,
        "highlight_line": line,
        "content": snippet,
    }


def create_graph_router() -> Any:
    """Create a FastAPI APIRouter exposing /api/v2/graph/* endpoints.

    Requires `fastapi` (`pip install "aegis-code-graph[api]"`).
    """
    from fastapi import APIRouter, HTTPException, Query
    from pydantic import BaseModel

    from aegis_code_graph import generate_graph

    router = APIRouter(prefix="/api/v2/graph", tags=["aegis-code-graph"])
    _cache: dict[tuple[str, str, str, str | None], GraphResult] = {}

    class AnalyzeRequest(BaseModel):
        repo_path: str
        model: str = "qwen2.5-coder:7b"
        base_url: str = "http://localhost:11434/v1"
        api_key: str = "ollama"
        sarif_path: str | None = None
        max_tokens_budget: int = 6000
        timeout: float = 300.0
        force_refresh: bool = False

    @router.post("/analyze", response_model=GraphResult)
    async def analyze_repo(req: AnalyzeRequest) -> GraphResult:
        root = Path(req.repo_path).resolve()
        if not root.is_dir():
            raise HTTPException(status_code=404, detail=f"Repository not found: {req.repo_path}")

        sha = get_git_head_sha(root) or "no-git"
        cache_key = (str(root), sha, req.model, req.sarif_path)
        if not req.force_refresh and cache_key in _cache:
            return _cache[cache_key]

        cfg = GraphConfig(
            base_url=req.base_url,
            api_key=req.api_key,
            model=req.model,
            sarif_path=req.sarif_path,
            max_tokens_budget=req.max_tokens_budget,
            timeout=req.timeout,
        )
        result = await generate_graph(root, config=cfg)
        _cache[cache_key] = result
        return result

    @router.get("/micro", response_model=MicroMethodGraph)
    async def get_micro_graph(
        repo_path: str = Query(...),
        file_path: str = Query(...),
    ) -> MicroMethodGraph:
        try:
            return extract_file_micro_graph(repo_path, file_path)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/snippet")
    async def get_snippet(
        repo_path: str = Query(...),
        file_path: str = Query(...),
        line: int = Query(1, ge=1),
        context_lines: int = Query(20, ge=1, le=200),
    ) -> dict[str, Any]:
        try:
            return read_code_snippet(repo_path, file_path, line=line, context_lines=context_lines)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
