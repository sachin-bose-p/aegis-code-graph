"""Async OpenAI-compatible LLM client for architecture graph synthesis."""

from __future__ import annotations

import json
import re
from typing import Any

from openai import AsyncOpenAI

from aegis_code_graph.models import ArchitectureGraph, GraphConfig, RepoContext

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)

_NOISY_IMPORT_PREFIXES: tuple[str, ...] = (
    "import java.lang.",
    "import java.util.",
    "import android.os.",
    "import android.util.",
    "import 'dart:core'",
    "import 'package:flutter/material.dart'",
    "import 'package:flutter/widgets.dart'",
    "from __future__",
    "from typing ",
    "import typing",
)


def _filter_high_signal_imports(imports: list[str]) -> list[str]:
    """Drop ubiquitous stdlib/UI-boilerplate imports to keep prompt tokens dense."""
    return [
        imp
        for imp in imports
        if not any(imp.startswith(prefix) for prefix in _NOISY_IMPORT_PREFIXES)
    ]


def format_context_prompt(context: RepoContext, config: GraphConfig) -> str:
    """Serialize a RepoContext into a compact prompt for architecture synthesis."""
    app_card_count = max(3, len(context.symbol_cards) or len(context.ast_map))
    effective_min = min(config.min_nodes, app_card_count)
    sections: list[str] = [
        f"# Repository: {context.repo_name}",
        f"Target node count: {effective_min}-{config.max_nodes} core architectural nodes.",
        "",
        "## File Tree",
        context.tree_summary or "(empty)",
    ]

    if context.manifests:
        sections.append("\n## Manifests & Metadata")
        for path, content in context.manifests.items():
            sections.append(f"### {path}\n```\n{content}\n```")

    if context.entrypoints:
        sections.append("\n## Entrypoints")
        for path, content in context.entrypoints.items():
            sections.append(f"### {path}\n```\n{content}\n```")

    if context.symbol_cards:
        sections.append("\n## AST Symbol Cards")
        for card in context.symbol_cards:
            header = f"- {card.file_path} ({card.language}, {card.symbol_type}, {card.loc} LOC) | symbol={card.primary_symbol}"
            parts = [header]
            if card.methods:
                parts.append(f"methods=[{', '.join(card.methods[:5])}]")
            if card.state_models:
                parts.append(f"state=[{', '.join(card.state_models[:5])}]")
            filtered_imports = _filter_high_signal_imports(card.imports)
            if filtered_imports:
                parts.append(f"imports=[{', '.join(filtered_imports[:8])}]")
            sections.append(" | ".join(parts))
    elif context.ast_map:
        sections.append("\n## AST Symbol & Import Map")
        for sym in context.ast_map:
            parts = [f"- {sym.file_path} ({sym.language})"]
            if sym.classes:
                parts.append(f"classes=[{', '.join(sym.classes)}]")
            if sym.functions:
                parts.append(f"funcs=[{', '.join(sym.functions)}]")
            filtered_imports = _filter_high_signal_imports(sym.imports)
            if filtered_imports:
                parts.append(f"imports=[{', '.join(filtered_imports[:10])}]")
            sections.append(" | ".join(parts))

    if context.verified_edges:
        sections.append("\n## Verified Import Edges (Deterministic)")
        for ve in context.verified_edges[:40]:
            syms = f" ({', '.join(ve.matched_symbols[:4])})" if ve.matched_symbols else ""
            sections.append(f"- {ve.source_file} -> {ve.target_file}{syms}")

    return "\n".join(sections)


def _build_system_prompt(config: GraphConfig) -> str:
    """Construct the system prompt with the ArchitectureGraph JSON schema."""
    schema_json = json.dumps(ArchitectureGraph.model_json_schema(), separators=(",", ":"))
    return (
        "You are a principal software architect. Given a repository's file tree, "
        "manifests, entrypoints, and AST symbol/import map, synthesize a clean, "
        "accurate architectural dependency graph.\n\n"
        "Rules:\n"
        f"1. Include {config.min_nodes} to {config.max_nodes} core architectural nodes "
        "(fewer only if the repo itself has fewer components).\n"
        "2. Use REAL class/symbol names from AST Symbol Cards (e.g., ContactViewModel, "
        "ContactRepository). NEVER invent synthetic umbrella names like 'Management Screens'.\n"
        "3. Do NOT turn `data class` DTOs, `enum class`es, or UI helper items (e.g., "
        "Contact, TransactionStatus, DashboardCardItem) into standalone State/Domain nodes — "
        "list them in `state_models` on their owning node instead.\n"
        "3. Group modules into logical layers using the allowed `layer` values: "
        '"ui", "state", "domain", "data", "infra", "external".\n'
        "4. Every `node.id` and `group.id` must be alphanumeric snake_case.\n"
        "5. Every `node.file_path` MUST be an exact relative path from the File Tree "
        '(or "" only for external services). Include `symbol_type`, `key_methods`, and `state_models`.\n'
        "6. Never write tautological summaries like 'Manages X state' or 'Handles X service'; "
        "describe concrete data flow and responsibilities.\n"
        "7. Never use generic 'uses' edges. Use concrete method/stream/route phrases "
        "(e.g., 'collects uiState', 'queries getAllContacts()') and set `edge_type` to "
        '"state_stream", "method_call", "data_io", or "navigation".\n'
        "8. Do NOT place unit/integration test files (test_*, *_test.*, unittest/) "
        "in UI/Domain/Data layers; focus on real application modules, activities, "
        "controllers, and services.\n"
        "9. Every edge `source` and `target` must reference a defined `node.id`.\n"
        "10. Respond ONLY with valid JSON matching this schema:\n"
        f"{schema_json}"
    )


def _extract_json_payload(raw_content: str) -> str:
    """Strip optional markdown code fences from an LLM response."""
    text = raw_content.strip()
    match = _CODE_FENCE_RE.match(text)
    if match:
        return match.group(1).strip()
    return text


async def synthesize_architecture(
    context: RepoContext,
    config: GraphConfig | None = None,
    client: Any | None = None,
) -> ArchitectureGraph:
    """Call an OpenAI-compatible endpoint to synthesize an ArchitectureGraph.

    Args:
        context: Scanned repository context.
        config: GraphConfig specifying endpoint, model, and node limits.
        client: Optional pre-configured AsyncOpenAI-compatible client (for testing).

    Returns:
        Parsed ArchitectureGraph Pydantic model.
    """
    cfg = config or GraphConfig()
    owns_client = client is None
    active_client = client or AsyncOpenAI(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        timeout=cfg.timeout,
        max_retries=0,
    )

    try:
        response = await active_client.chat.completions.create(
            model=cfg.model,
            temperature=cfg.temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _build_system_prompt(cfg)},
                {"role": "user", "content": format_context_prompt(context, cfg)},
            ],
        )
    finally:
        if owns_client and hasattr(active_client, "close"):
            await active_client.close()

    raw = response.choices[0].message.content or ""
    cleaned = _extract_json_payload(raw)
    return ArchitectureGraph.model_validate_json(cleaned)
