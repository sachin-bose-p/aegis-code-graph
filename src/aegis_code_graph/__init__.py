"""aegis-code-graph: Lightweight local-first repository-to-architecture diagram engine."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from aegis_code_graph.compiler import compile_mermaid, compile_react_flow
from aegis_code_graph.llm_client import synthesize_architecture
from aegis_code_graph.models import (
    ArchitectureGraph,
    FileSymbols,
    GraphConfig,
    GraphEdge,
    GraphNode,
    GraphResult,
    RepoContext,
    SubgraphGroup,
    ValidationReport,
)
from aegis_code_graph.scanner import scan_repository
from aegis_code_graph.validator import validate_graph

__all__ = [
    "ArchitectureGraph",
    "FileSymbols",
    "GraphConfig",
    "GraphEdge",
    "GraphNode",
    "GraphResult",
    "RepoContext",
    "SubgraphGroup",
    "ValidationReport",
    "compile_mermaid",
    "compile_react_flow",
    "generate_graph",
    "generate_graph_sync",
    "scan_repository",
    "validate_graph",
]


async def generate_graph(
    repo_path: str | Path,
    config: GraphConfig | None = None,
    client: Any | None = None,
) -> GraphResult:
    """Scan a repository, synthesize its architecture graph, validate, and compile outputs."""
    cfg = config or GraphConfig()
    context = scan_repository(repo_path, config=cfg)
    raw_graph = await synthesize_architecture(context, config=cfg, client=client)
    clean_graph, report = validate_graph(raw_graph, context.all_files)
    mermaid = compile_mermaid(clean_graph)
    react_flow = compile_react_flow(clean_graph)

    return GraphResult(
        graph=clean_graph,
        mermaid=mermaid,
        react_flow=react_flow,
        validation=report,
        context_tokens=context.estimated_tokens,
    )


def generate_graph_sync(
    repo_path: str | Path,
    config: GraphConfig | None = None,
    client: Any | None = None,
) -> GraphResult:
    """Synchronous wrapper around generate_graph()."""
    return asyncio.run(generate_graph(repo_path, config=config, client=client))
