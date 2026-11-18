"""Deterministic compilers from ArchitectureGraph to Mermaid.js and React Flow JSON."""

from __future__ import annotations

from typing import Any

from aegis_code_graph.models import ArchitectureGraph, GraphNode, LayerType, SubgraphGroup

LAYER_TITLES: dict[LayerType, str] = {
    "ui": "UI & Presentation",
    "state": "State Management",
    "domain": "Domain & Business Logic",
    "data": "Data & Repositories",
    "infra": "Infrastructure & Platform",
    "external": "External Services",
}

LAYER_ORDER: tuple[LayerType, ...] = ("ui", "state", "domain", "data", "infra", "external")

MERMAID_CLASS_DEFS: tuple[str, ...] = (
    "classDef ui fill:#e0f2fe,stroke:#0284c7,stroke-width:2px,color:#0c4a6e;",
    "classDef state fill:#f3e8ff,stroke:#9333ea,stroke-width:2px,color:#581c87;",
    "classDef domain fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#14532d;",
    "classDef data fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#78350f;",
    "classDef infra fill:#f1f5f9,stroke:#475569,stroke-width:2px,color:#0f172a;",
    "classDef external fill:#ffe4e6,stroke:#e11d48,stroke-width:2px,color:#881337,stroke-dasharray:5 5;",
)


def _escape_mermaid_text(text: str) -> str:
    """Escape characters that break Mermaid quoted labels."""
    return text.replace('"', "'").replace("\n", " ").strip()


def _resolve_groups(graph: ArchitectureGraph) -> tuple[list[SubgraphGroup], list[GraphNode]]:
    """Return effective subgraph groups and any ungrouped nodes."""
    if graph.groups:
        grouped_ids = {nid for g in graph.groups for nid in g.node_ids}
        ungrouped = [n for n in graph.nodes if n.id not in grouped_ids]
        return graph.groups, ungrouped

    # Fallback: group automatically by architectural layer
    by_layer: dict[LayerType, list[str]] = {layer: [] for layer in LAYER_ORDER}
    for node in graph.nodes:
        by_layer[node.layer].append(node.id)

    auto_groups = [
        SubgraphGroup(
            id=f"layer_{layer}",
            title=LAYER_TITLES[layer],
            node_ids=node_ids,
        )
        for layer, node_ids in by_layer.items()
        if node_ids
    ]
    return auto_groups, []


def compile_mermaid(graph: ArchitectureGraph) -> str:
    """Compile a validated ArchitectureGraph into a deterministic Mermaid.js flowchart."""
    lines: list[str] = ["flowchart TD"]
    node_by_id = {n.id: n for n in graph.nodes}
    groups, ungrouped = _resolve_groups(graph)

    emitted_nodes: set[str] = set()
    for group in groups:
        member_nodes = [node_by_id[nid] for nid in group.node_ids if nid in node_by_id]
        if not member_nodes:
            continue
        title = _escape_mermaid_text(group.title)
        lines.append(f'    subgraph {group.id}["{title}"]')
        for node in member_nodes:
            label = _escape_mermaid_text(node.label)
            if node.key_methods:
                methods_preview = ", ".join(_escape_mermaid_text(m.split("(")[0].split()[-1] + "()") for m in node.key_methods[:2])
                lines.append(f'        {node.id}["{label}<br/><i>{methods_preview}</i>"]')
            else:
                lines.append(f'        {node.id}["{label}"]')
            emitted_nodes.add(node.id)
        lines.append("    end")

    for node in ungrouped:
        if node.id not in emitted_nodes:
            label = _escape_mermaid_text(node.label)
            if node.key_methods:
                methods_preview = ", ".join(_escape_mermaid_text(m.split("(")[0].split()[-1] + "()") for m in node.key_methods[:2])
                lines.append(f'    {node.id}["{label}<br/><i>{methods_preview}</i>"]')
            else:
                lines.append(f'    {node.id}["{label}"]')
            emitted_nodes.add(node.id)

    if graph.edges:
        lines.append("")
        for edge in graph.edges:
            rel = _escape_mermaid_text(edge.relation)
            if rel:
                lines.append(f'    {edge.source} -->|"{rel}"| {edge.target}')
            else:
                lines.append(f"    {edge.source} --> {edge.target}")

    if graph.nodes:
        lines.append("")
        for class_def in MERMAID_CLASS_DEFS:
            lines.append(f"    {class_def}")
        for node in graph.nodes:
            lines.append(f"    class {node.id} {node.layer};")

    click_lines: list[str] = []
    for node in graph.nodes:
        if node.file_path:
            fpath = _escape_mermaid_text(node.file_path)
            summary = _escape_mermaid_text(node.summary)
            click_lines.append(f'    click {node.id} "{fpath}" "{summary}"')
    if click_lines:
        lines.append("")
        lines.extend(click_lines)

    return "\n".join(lines) + "\n"


def compile_react_flow(graph: ArchitectureGraph) -> dict[str, Any]:
    """Compile a validated ArchitectureGraph into a React Flow / Cytoscape JSON payload."""
    groups, _ = _resolve_groups(graph)
    node_to_group: dict[str, str] = {}
    for group in groups:
        for nid in group.node_ids:
            node_to_group.setdefault(nid, group.id)

    layer_row: dict[LayerType, int] = {layer: idx for idx, layer in enumerate(LAYER_ORDER)}
    layer_col_counts: dict[LayerType, int] = {layer: 0 for layer in LAYER_ORDER}

    rf_nodes: list[dict[str, Any]] = []
    for node in graph.nodes:
        col = layer_col_counts[node.layer]
        layer_col_counts[node.layer] += 1
        row = layer_row[node.layer]

        rf_nodes.append(
            {
                "id": node.id,
                "type": "architectureNode",
                "position": {"x": col * 280, "y": row * 160},
                "data": {
                    "label": node.label,
                    "file_path": node.file_path,
                    "layer": node.layer,
                    "symbol_type": node.symbol_type,
                    "summary": node.summary,
                    "key_methods": list(node.key_methods),
                    "state_models": list(node.state_models),
                    "loc": node.loc,
                    "fan_in": node.fan_in,
                    "fan_out": node.fan_out,
                    "security_flags": [f.model_dump() for f in node.security_flags],
                    "group_id": node_to_group.get(node.id),
                },
            }
        )

    rf_edges: list[dict[str, Any]] = [
        {
            "id": edge.id or f"e_{idx}_{edge.source}_{edge.target}",
            "source": edge.source,
            "target": edge.target,
            "type": "aegisSemanticEdge",
            "label": edge.relation,
            "animated": edge.edge_type == "state_stream",
            "data": {
                "edge_type": edge.edge_type,
                "relation": edge.relation,
                "data_payload": edge.data_payload,
                "verified": edge.verified,
            },
        }
        for idx, edge in enumerate(graph.edges)
    ]

    rf_groups: list[dict[str, Any]] = [
        {
            "id": group.id,
            "title": group.title,
            "node_ids": list(group.node_ids),
        }
        for group in groups
    ]

    layer_counts: dict[str, int] = {}
    sec_count = 0
    for node in graph.nodes:
        layer_counts[node.layer] = layer_counts.get(node.layer, 0) + 1
        sec_count += len(node.security_flags)

    return {
        "project_name": graph.project_name,
        "architecture_pattern": graph.architecture_pattern,
        "nodes": rf_nodes,
        "edges": rf_edges,
        "groups": rf_groups,
        "stats": {
            "node_count": len(rf_nodes),
            "edge_count": len(rf_edges),
            "verified_edges": sum(1 for e in graph.edges if e.verified),
            "security_finding_count": sec_count,
            "layers": layer_counts,
        },
    }


# v0.2 convenience aliases
to_mermaid = compile_mermaid
to_react_flow_json = compile_react_flow
