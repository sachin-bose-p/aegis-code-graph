"""Pure Tree-sitter micro-graph extractor for instant (<50ms) file drilldown."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from aegis_code_graph.ast_extractor import (
    _CLASS_NODE_TYPES,
    _FUNCTION_NODE_TYPES,
    _extract_identifier,
    _extract_method_signature,
    detect_language,
)
from aegis_code_graph.compiler import compile_react_flow
from aegis_code_graph.models import (
    ArchitectureGraph,
    FeatureFlowGraph,
    FileSymbolCard,
    GraphConfig,
    GraphEdge,
    GraphNode,
    LayerType,
    MicroMethodGraph,
    MicroMethodInfo,
    PackageDependency,
)
from aegis_code_graph.telemetry import compute_fan_metrics
from aegis_code_graph.validator import sanitize_id

_CALL_NODE_TYPES: frozenset[str] = frozenset(
    {
        "call_expression",
        "method_invocation",
        "call",
        "navigation_expression",
    }
)
_VISIBILITY_WORDS: tuple[str, ...] = ("private", "protected", "internal", "public")
_CALL_IDENT_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_PROP_RE = re.compile(
    r"^\s*(?:private\s+|protected\s+|public\s+|internal\s+|lateinit\s+|final\s+|static\s+|const\s+)*"
    r"(?:val|var|let|const|StateFlow|MutableStateFlow|LiveData)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)


def _infer_visibility(node: Any) -> str:
    """Determine method visibility from leading modifiers or Python naming."""
    if not node.text:
        return "public"
    header = node.text.decode("utf-8", errors="ignore").split("{", 1)[0][:60].lower()
    for vis in _VISIBILITY_WORDS:
        if re.search(rf"\b{vis}\b", header):
            return vis
    name = _extract_identifier(node) or ""
    if name.startswith("_") and not name.startswith("__"):
        return "private"
    return "public"


def _collect_calls(node: Any, self_name: str) -> list[str]:
    """Collect called function/method names inside a function AST subtree."""
    calls: list[str] = []
    if node.text:
        body_text = node.text.decode("utf-8", errors="ignore")
        # Skip the signature line itself
        after_sig = body_text.split("{", 1)[1] if "{" in body_text else body_text
        for match in _CALL_IDENT_RE.finditer(after_sig):
            callee = match.group(1)
            if callee != self_name and callee not in {"if", "for", "while", "when", "switch", "catch", "return"}:
                if callee not in calls:
                    calls.append(callee)
    return calls[:20]


def extract_file_micro_graph(
    repo_path: str | Path,
    file_path: str,
) -> MicroMethodGraph:
    """Extract an intra-file method & call graph using Tree-sitter (zero LLM)."""
    root = Path(repo_path).resolve()
    rel_posix = file_path.replace("\\", "/").lstrip("./")
    target = (root / rel_posix).resolve()
    if not target.is_file():
        raise FileNotFoundError(f"Source file not found: {rel_posix}")

    language = detect_language(target) or "unknown"
    raw_bytes = target.read_bytes()
    source_text = raw_bytes.decode("utf-8", errors="ignore")

    methods: list[MicroMethodInfo] = []
    class_name = target.stem

    if language != "unknown":
        try:
            import tree_sitter_language_pack as tslp

            parser = tslp.get_parser(language)
            tree = parser.parse(raw_bytes)

            def walk(node: Any) -> None:
                nonlocal class_name
                if node.type in _CLASS_NODE_TYPES:
                    cname = _extract_identifier(node)
                    if cname and class_name == target.stem:
                        class_name = cname
                if node.type in _FUNCTION_NODE_TYPES:
                    mname = _extract_identifier(node)
                    if mname:
                        sig = _extract_method_signature(node) or mname
                        methods.append(
                            MicroMethodInfo(
                                name=mname,
                                signature=sig,
                                visibility=_infer_visibility(node),
                                line=node.start_point[0] + 1,
                                calls=_collect_calls(node, mname),
                            )
                        )
                    return
                for child in node.children:
                    walk(child)

            walk(tree.root_node)
        except Exception:
            pass

    method_names = {m.name for m in methods}
    internal_edges: list[GraphEdge] = []
    edge_idx = 0
    for m in methods:
        for callee in m.calls:
            if callee in method_names:
                internal_edges.append(
                    GraphEdge(
                        id=f"micro_{edge_idx}_{m.name}_{callee}",
                        source=m.name,
                        target=callee,
                        edge_type="method_call",
                        relation=f"calls {callee}()",
                        verified=True,
                    )
                )
                edge_idx += 1

    state_props = list(dict.fromkeys(m.group(1) for m in _PROP_RE.finditer(source_text)))[:20]

    return MicroMethodGraph(
        file_path=rel_posix,
        class_name=class_name,
        language=language,
        methods=methods,
        state_properties=state_props,
        internal_edges=internal_edges,
    )


_PKG_CATEGORIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("compose", "material", "flutter/material", "flutter/cupertino", "swiftui", "uikit", "react"), "UI / Framework"),
    (("retrofit", "okhttp", "ktor", "dio", "http", "axios", "fetch", "alamofire"), "Networking"),
    (("coroutines", "flow", "rxjava", "bloc", "riverpod", "provider", "redux", "zustand", "lifecycle"), "State & Async"),
    (("room", "sqlite", "sqflite", "hive", "shared_preferences", "datastore", "realm", "sqlalchemy", "prisma"), "Storage / DB"),
    (("hilt", "dagger", "koin", "get_it", "injectable"), "Dependency Injection"),
    (("firebase", "auth", "jwt", "security", "crypto", "keystore"), "Security / Auth"),
)
_STDLIB_PREFIXES: tuple[str, ...] = ("java.lang", "java.util", "kotlin.", "dart:core", "dart:async", "os", "sys", "typing")


def _extract_package_root(raw_import: str) -> str | None:
    """Extract a clean package/library identifier from an import statement."""
    text = raw_import.strip()
    if text.startswith("import "):
        text = text[len("import ") :].strip()
    elif text.startswith("from "):
        text = text[len("from ") :].split(" import ", 1)[0].strip()

    text = text.strip("'\";").split(" as ")[0].strip()
    if not text or text.startswith("."):
        return None
    if text.startswith("package:"):
        # e.g. package:flutter/material.dart -> flutter/material
        pkg = text[len("package:") :].split("/")[0]
        return pkg
    if "/" in text:
        return text.split("/")[0]
    parts = text.split(".")
    return ".".join(parts[:3]) if len(parts) >= 3 else text


def _categorize_package(pkg_name: str) -> str:
    low = pkg_name.lower()
    for markers, cat in _PKG_CATEGORIES:
        if any(m in low for m in markers):
            return cat
    return "Library"


def _infer_layer_from_card(card: FileSymbolCard) -> LayerType:
    stype = card.symbol_type.lower()
    path_low = card.file_path.lower()
    if stype in {"composable", "screen", "activity", "fragment", "statelesswidget", "statefulwidget"} or "/ui/" in path_low:
        return "ui"
    if stype in {"viewmodel", "bloc", "cubit", "changenotifier"} or "state" in path_low:
        return "state"
    if stype in {"usecase"} or "domain" in path_low:
        return "domain"
    if stype in {"repository", "dao", "apiclient", "entity"} or "/data/" in path_low:
        return "data"
    return "infra"


def extract_feature_flow(
    repo_path: str | Path,
    file_path: str,
    max_depth: int = 3,
    sarif_path: str | None = None,
) -> FeatureFlowGraph:
    """Extract a deterministic vertical-slice flow graph for a selected screen/feature.

    Includes 1-hop upstream callers, transitive downstream dependencies,
    categorized external packages used, and the entry file's micro-graph.
    """
    from aegis_code_graph.scanner import scan_repository

    rel_entry = file_path.replace("\\", "/").lstrip("./")
    ctx = scan_repository(
        repo_path,
        config=GraphConfig(max_tokens_budget=24000, ast_token_budget=16000, sarif_path=sarif_path),
    )
    cards_by_path = {c.file_path: c for c in ctx.symbol_cards}
    if rel_entry not in cards_by_path:
        raise FileNotFoundError(f"Feature file not found in scanned sources: {rel_entry}")

    # Build adjacency for BFS
    outbound: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    inbound: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for ve in ctx.verified_edges:
        outbound[ve.source_file].append((ve.target_file, ve.matched_symbols))
        inbound[ve.target_file].append((ve.source_file, ve.matched_symbols))

    visited: set[str] = {rel_entry}
    queue: deque[tuple[str, int]] = deque([(rel_entry, 0)])
    while queue:
        curr, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for nxt, _ in outbound.get(curr, []):
            if nxt not in visited and nxt in cards_by_path:
                visited.add(nxt)
                queue.append((nxt, depth + 1))

    # Include 1-hop upstream callers
    for caller, _ in inbound.get(rel_entry, []):
        if caller in cards_by_path:
            visited.add(caller)

    fan_in_map, fan_out_map = compute_fan_metrics(ctx.verified_edges)
    used_ids: set[str] = set()
    path_to_id: dict[str, str] = {}
    nodes: list[GraphNode] = []

    pkg_users: dict[str, set[str]] = defaultdict(set)
    internal_pkgs = {c.package_name for c in ctx.symbol_cards if c.package_name}

    for fpath in sorted(visited):
        card = cards_by_path[fpath]
        nid = sanitize_id(card.primary_symbol or Path(fpath).stem, used_ids)
        path_to_id[fpath] = nid
        nodes.append(
            GraphNode(
                id=nid,
                label=card.primary_symbol or Path(fpath).stem,
                file_path=fpath,
                layer=_infer_layer_from_card(card),
                symbol_type=card.symbol_type,
                summary=f"{card.symbol_type} in {Path(fpath).name} ({card.loc} LOC)",
                key_methods=card.methods[:5],
                state_models=card.state_models[:5],
                loc=card.loc,
                fan_in=fan_in_map.get(fpath, 0),
                fan_out=fan_out_map.get(fpath, 0),
                security_flags=ctx.sarif_findings.get(fpath, []),
            )
        )
        for imp in card.imports:
            pkg = _extract_package_root(imp)
            if not pkg or any(pkg.startswith(s) for s in _STDLIB_PREFIXES):
                continue
            if any(pkg.startswith(ip) for ip in internal_pkgs if ip):
                continue
            pkg_users[pkg].add(nid)

    edges: list[GraphEdge] = []
    edge_idx = 0
    for ve in ctx.verified_edges:
        if ve.source_file in path_to_id and ve.target_file in path_to_id:
            syms = ", ".join(ve.matched_symbols[:3]) or "imports"
            edges.append(
                GraphEdge(
                    id=f"ff_{edge_idx}",
                    source=path_to_id[ve.source_file],
                    target=path_to_id[ve.target_file],
                    edge_type="method_call",
                    relation=f"calls {syms}",
                    verified=True,
                )
            )
            edge_idx += 1

    # Add top external packages used by this feature flow
    packages_used = [
        PackageDependency(
            name=pkg,
            category=_categorize_package(pkg),
            used_by=sorted(users),
        )
        for pkg, users in sorted(pkg_users.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]

    for pkg_dep in packages_used[:8]:
        pid = sanitize_id(f"pkg_{pkg_dep.name}", used_ids)
        nodes.append(
            GraphNode(
                id=pid,
                label=pkg_dep.name,
                file_path="",
                layer="external",
                symbol_type=pkg_dep.category,
                summary=f"External {pkg_dep.category} dependency",
            )
        )
        for user_id in pkg_dep.used_by:
            edges.append(
                GraphEdge(
                    id=f"ff_{edge_idx}",
                    source=user_id,
                    target=pid,
                    edge_type="data_io",
                    relation=f"imports {pkg_dep.category}",
                    verified=True,
                )
            )
            edge_idx += 1

    entry_card = cards_by_path[rel_entry]
    arch_graph = ArchitectureGraph(
        project_name=f"{ctx.repo_name} :: {entry_card.primary_symbol}",
        architecture_pattern="Feature Flow Slice",
        groups=[],
        nodes=nodes,
        edges=edges,
    )

    return FeatureFlowGraph(
        entry_file=rel_entry,
        entry_symbol=entry_card.primary_symbol,
        packages_used=packages_used,
        entry_micro_graph=extract_file_micro_graph(repo_path, rel_entry),
        graph=arch_graph,
        react_flow=compile_react_flow(arch_graph),
    )
