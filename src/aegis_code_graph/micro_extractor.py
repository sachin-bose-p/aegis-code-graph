"""Pure Tree-sitter micro-graph extractor for instant (<50ms) file drilldown."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from aegis_code_graph.ast_extractor import (
    _CLASS_NODE_TYPES,
    _FUNCTION_NODE_TYPES,
    _extract_identifier,
    _extract_method_signature,
    detect_language,
)
from aegis_code_graph.models import GraphEdge, MicroMethodGraph, MicroMethodInfo

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
