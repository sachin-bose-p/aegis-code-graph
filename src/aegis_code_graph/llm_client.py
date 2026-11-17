"""Async OpenAI-compatible LLM client for architecture graph synthesis."""

from __future__ import annotations

import json
import re
from typing import Any

from openai import AsyncOpenAI

from aegis_code_graph.models import ArchitectureGraph, GraphConfig, RepoContext

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)


def format_context_prompt(context: RepoContext, config: GraphConfig) -> str:
    """Serialize a RepoContext into a compact prompt for architecture synthesis."""
    sections: list[str] = [
        f"# Repository: {context.repo_name}",
        f"Target node count: {config.min_nodes}-{config.max_nodes} core architectural nodes.",
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

    if context.ast_map:
        sections.append("\n## AST Symbol & Import Map")
        for sym in context.ast_map:
            parts = [f"- {sym.file_path} ({sym.language})"]
            if sym.classes:
                parts.append(f"classes=[{', '.join(sym.classes)}]")
            if sym.functions:
                parts.append(f"funcs=[{', '.join(sym.functions)}]")
            if sym.imports:
                parts.append(f"imports=[{', '.join(sym.imports[:10])}]")
            sections.append(" | ".join(parts))

    return "\n".join(sections)


def _build_system_prompt(config: GraphConfig) -> str:
    """Construct the system prompt with the ArchitectureGraph JSON schema."""
    schema_json = json.dumps(ArchitectureGraph.model_json_schema(), indent=2)
    return (
        "You are a principal software architect. Given a repository's file tree, "
        "manifests, entrypoints, and AST symbol/import map, synthesize a clean, "
        "accurate architectural dependency graph.\n\n"
        "Rules:\n"
        f"1. Include {config.min_nodes} to {config.max_nodes} core architectural nodes "
        "(fewer only if the repo itself has fewer components).\n"
        "2. Group modules into logical layers using the allowed `layer` values: "
        '"ui", "state", "domain", "data", "infra", "external".\n'
        "3. Every `node.id` and `group.id` must be alphanumeric snake_case.\n"
        "4. Every `node.file_path` MUST be an exact relative path from the File Tree "
        '(or "" only for external services).\n'
        "5. Every edge `source` and `target` must reference a defined `node.id`.\n"
        "6. Respond ONLY with valid JSON matching this schema:\n"
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
