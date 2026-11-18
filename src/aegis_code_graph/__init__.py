"""aegis-code-graph: Lightweight local-first repository-to-architecture diagram engine."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from aegis_code_graph.api import create_graph_router, read_code_snippet
from aegis_code_graph.compiler import (
    compile_mermaid,
    compile_react_flow,
    to_mermaid,
    to_react_flow_json,
)
from aegis_code_graph.llm_client import synthesize_architecture
from aegis_code_graph.micro_extractor import extract_file_micro_graph
from aegis_code_graph.models import (
    ArchitectureGraph,
    FileSymbolCard,
    FileSymbols,
    GraphConfig,
    GraphEdge,
    GraphNode,
    GraphResult,
    MicroMethodGraph,
    MicroMethodInfo,
    RepoContext,
    SecurityFlag,
    SubgraphGroup,
    ValidationReport,
    VerifiedCallEdge,
)
from aegis_code_graph.scanner import scan_repository
from aegis_code_graph.validator import validate_graph

__all__ = [
    "ArchitectureGraph",
    "FileSymbolCard",
    "FileSymbols",
    "GraphConfig",
    "GraphEdge",
    "GraphNode",
    "GraphResult",
    "MicroMethodGraph",
    "MicroMethodInfo",
    "RepoContext",
    "SecurityFlag",
    "SubgraphGroup",
    "ValidationReport",
    "VerifiedCallEdge",
    "compile_mermaid",
    "compile_react_flow",
    "create_graph_router",
    "extract_file_micro_graph",
    "generate_graph",
    "generate_graph_sync",
    "read_code_snippet",
    "scan_repository",
    "to_mermaid",
    "to_react_flow_json",
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
    clean_graph, report = validate_graph(raw_graph, context.all_files, context=context)
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
