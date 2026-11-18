"""Deterministic path, ID, and edge validator for synthesized architecture graphs."""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from pathlib import Path

from aegis_code_graph.models import (
    ArchitectureGraph,
    FileSymbolCard,
    GraphEdge,
    GraphNode,
    RepoContext,
    SubgraphGroup,
    ValidationReport,
)
from aegis_code_graph.telemetry import compute_fan_metrics

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
    context: RepoContext | None = None,
) -> tuple[ArchitectureGraph, ValidationReport]:
    """Validate, repair, and enrich an LLM-synthesized ArchitectureGraph.

    - Verifies every node.file_path exists in valid_files (fuzzy-matches or strips).
    - Sanitizes node IDs and group IDs for Mermaid compatibility.
    - Remaps and filters edges pointing to non-existent node IDs.
    - Filters non-existent node IDs from SubgraphGroup.node_ids.
    - Enriches nodes with deterministic LOC, fan-in/out, methods, and SARIF security flags.
    """
    valid_file_set = {f.replace("\\", "/").lstrip("./") for f in valid_files}
    sorted_files = sorted(valid_file_set)
    report = ValidationReport()

    cards_by_path: dict[str, FileSymbolCard] = (
        {c.file_path: c for c in context.symbol_cards} if context else {}
    )
    verified_pairs: set[tuple[str, str]] = (
        {(ve.source_file, ve.target_file) for ve in context.verified_edges} if context else set()
    )
    fan_in_map, fan_out_map = (
        compute_fan_metrics(context.verified_edges) if context and context.verified_edges else ({}, {})
    )
    sarif_map = context.sarif_findings if context else {}

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

        card = cards_by_path.get(resolved_path)
        symbol_type = node.symbol_type
        if (not symbol_type or symbol_type == "Module") and card and card.symbol_type:
            symbol_type = card.symbol_type

        key_methods = node.key_methods or (card.methods[:5] if card else [])
        state_models = node.state_models or (card.state_models[:5] if card else [])
        loc = node.loc or (card.loc if card else 0)
        fan_in = node.fan_in or fan_in_map.get(resolved_path, 0)
        fan_out = node.fan_out or fan_out_map.get(resolved_path, 0)
        security_flags = node.security_flags or sarif_map.get(resolved_path, [])

        validated_nodes.append(
            GraphNode(
                id=new_id,
                label=node.label,
                file_path=resolved_path,
                layer=node.layer,
                symbol_type=symbol_type,
                summary=node.summary,
                key_methods=key_methods,
                state_models=state_models,
                loc=loc,
                fan_in=fan_in,
                fan_out=fan_out,
                security_flags=security_flags,
            )
        )

    node_by_id = {n.id: n for n in validated_nodes}
    valid_node_id_set = set(node_by_id)

    # Validate and deduplicate edges
    validated_edges: list[GraphEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for idx, edge in enumerate(graph.edges):
        src = id_map.get(edge.source, edge.source)
        tgt = id_map.get(edge.target, edge.target)
        if src not in valid_node_id_set or tgt not in valid_node_id_set:
            report.removed_edges.append((edge.source, edge.target, edge.relation))
            continue
        key = (src, tgt, edge.relation)
        if key in seen_edges:
            continue
        seen_edges.add(key)

        src_path = node_by_id[src].file_path
        tgt_path = node_by_id[tgt].file_path
        is_verified = edge.verified or (bool(src_path and tgt_path) and (src_path, tgt_path) in verified_pairs)
        if is_verified:
            report.verified_edge_count += 1

        validated_edges.append(
            GraphEdge(
                id=edge.id or f"e_{idx}_{src}_{tgt}",
                source=src,
                target=tgt,
                edge_type=edge.edge_type,
                relation=edge.relation,
                data_payload=edge.data_payload,
                verified=is_verified,
            )
        )

    # Fallback fan-in / fan-out from validated graph edges when AST edges weren't present
    if not fan_in_map and not fan_out_map:
        for edge in validated_edges:
            node_by_id[edge.source].fan_out += 1
            node_by_id[edge.target].fan_in += 1

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
