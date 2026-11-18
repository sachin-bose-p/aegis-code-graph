"""Deterministic path, ID, and edge validator for synthesized architecture graphs."""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from pathlib import Path

from aegis_code_graph.models import (
    ArchitectureGraph,
    GraphEdge,
    GraphNode,
    SubgraphGroup,
    ValidationReport,
)

_INVALID_ID_CHARS_RE = re.compile(r"[^a-zA-Z0-9_]")
_MERMAID_RESERVED_IDS: frozenset[str] = frozenset(
    {"end", "subgraph", "graph", "flowchart", "class", "classdef", "style", "click", "linkstyle"}
)


def sanitize_id(raw_id: str, used_ids: set[str] | None = None) -> str:
    """Sanitize an identifier so it is safe for Mermaid.js and React Flow."""
    cleaned = _INVALID_ID_CHARS_RE.sub("_", raw_id.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "node"
    if cleaned[0].isdigit():
        cleaned = f"n_{cleaned}"
    if cleaned.lower() in _MERMAID_RESERVED_IDS:
        cleaned = f"{cleaned}_node"

    if used_ids is None:
        return cleaned

    candidate = cleaned
    counter = 2
    while candidate in used_ids:
        candidate = f"{cleaned}_{counter}"
        counter += 1
    used_ids.add(candidate)
    return candidate


def _resolve_file_path(raw_path: str, valid_files: set[str], sorted_files: list[str]) -> tuple[str, str]:
    """Verify a file path against valid_files, fuzzy-matching or stripping if missing.

    Returns:
        (resolved_path, status) where status is one of:
        'exact', 'fuzzy', 'stripped', 'empty'.
    """
    normalized = raw_path.strip().replace("\\", "/").lstrip("./")
    if not normalized:
        return "", "empty"

    if normalized in valid_files:
        return normalized, "exact"

    # Prefer matching against files with the same extension when available
    target_path = Path(normalized)
    target_name = target_path.name
    target_ext = target_path.suffix.lower()
    candidates = (
        [f for f in sorted_files if Path(f).suffix.lower() == target_ext]
        if target_ext
        else sorted_files
    )
    if not candidates:
        candidates = sorted_files

    # Try exact basename match if unambiguous
    basename_matches = [f for f in candidates if Path(f).name == target_name]
    if len(basename_matches) == 1:
        return basename_matches[0], "fuzzy"

    # Fuzzy match against candidate relative paths
    close = difflib.get_close_matches(normalized, candidates, n=1, cutoff=0.72)
    if close:
        return close[0], "fuzzy"

    return "", "stripped"


def validate_graph(
    graph: ArchitectureGraph,
    valid_files: Iterable[str],
) -> tuple[ArchitectureGraph, ValidationReport]:
    """Validate and repair an LLM-synthesized ArchitectureGraph.

    - Verifies every node.file_path exists in valid_files (fuzzy-matches or strips).
    - Sanitizes node IDs and group IDs for Mermaid compatibility.
    - Remaps and filters edges pointing to non-existent node IDs.
    - Filters non-existent node IDs from SubgraphGroup.node_ids.
    """
    valid_file_set = {f.replace("\\", "/").lstrip("./") for f in valid_files}
    sorted_files = sorted(valid_file_set)
    report = ValidationReport()

    used_node_ids: set[str] = set()
    id_map: dict[str, str] = {}
    validated_nodes: list[GraphNode] = []

    for node in graph.nodes:
        new_id = sanitize_id(node.id, used_node_ids)
        if new_id != node.id:
            report.sanitized_ids[node.id] = new_id
        id_map[node.id] = new_id

        resolved_path, status = _resolve_file_path(node.file_path, valid_file_set, sorted_files)
        if status == "fuzzy":
            report.fuzzy_matched_paths[node.file_path] = resolved_path
        elif status == "stripped":
            report.stripped_paths.append(new_id)

        validated_nodes.append(
            GraphNode(
                id=new_id,
                label=node.label,
                file_path=resolved_path,
                layer=node.layer,
                summary=node.summary,
            )
        )

    valid_node_id_set = {n.id for n in validated_nodes}

    # Validate and deduplicate edges
    validated_edges: list[GraphEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in graph.edges:
        src = id_map.get(edge.source, edge.source)
        tgt = id_map.get(edge.target, edge.target)
        if src not in valid_node_id_set or tgt not in valid_node_id_set:
            report.removed_edges.append((edge.source, edge.target, edge.relation))
            continue
        key = (src, tgt, edge.relation)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        validated_edges.append(GraphEdge(source=src, target=tgt, relation=edge.relation))

    # Validate subgraph groups
    used_group_ids: set[str] = set()
    validated_groups: list[SubgraphGroup] = []
    for group in graph.groups:
        gid = sanitize_id(group.id, used_group_ids)
        kept_ids: list[str] = []
        for nid in group.node_ids:
            mapped = id_map.get(nid, nid)
            if mapped in valid_node_id_set:
                if mapped not in kept_ids:
                    kept_ids.append(mapped)
            else:
                report.removed_group_refs += 1
        if kept_ids:
            validated_groups.append(
                SubgraphGroup(id=gid, title=group.title, node_ids=kept_ids)
            )

    cleaned_graph = ArchitectureGraph(
        project_name=graph.project_name,
        architecture_pattern=graph.architecture_pattern,
        groups=validated_groups,
        nodes=validated_nodes,
        edges=validated_edges,
    )
    return cleaned_graph, report
