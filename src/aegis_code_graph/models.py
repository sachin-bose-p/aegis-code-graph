"""Pydantic v2 schemas for aegis-code-graph."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

LayerType = Literal["ui", "state", "domain", "data", "infra", "external"]


class GraphNode(BaseModel):
    """A single architectural component in the codebase."""

    id: str = Field(..., description="Alphanumeric snake_case ID, e.g., auth_service")
    label: str = Field(..., description="Human-readable component name")
    file_path: str = Field(..., description="Exact relative path from repo root")
    layer: LayerType = Field(..., description="Architectural layer")
    summary: str = Field(..., description="One-line responsibility of this component")


class GraphEdge(BaseModel):
    """A directed dependency or interaction between two components."""

    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    relation: str = Field(..., description="Short verb phrase, e.g., 'authenticates via'")


class SubgraphGroup(BaseModel):
    """A logical grouping of architectural nodes."""

    id: str = Field(..., description="Alphanumeric snake_case group ID")
    title: str = Field(..., description="Human-readable group title")
    node_ids: list[str] = Field(default_factory=list, description="Member node IDs")


class ArchitectureGraph(BaseModel):
    """Complete synthesized architecture graph."""

    project_name: str
    architecture_pattern: str = Field(
        ..., description="e.g., 'Flutter BLoC Clean Architecture'"
    )
    groups: list[SubgraphGroup] = Field(default_factory=list)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class GraphConfig(BaseModel):
    """Configuration for scanning, LLM synthesis, and compilation."""

    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    model: str = "qwen2.5-coder:7b"
    max_tokens_budget: int = Field(default=6000, ge=1000, le=128000)
    ast_token_budget: int = Field(default=3000, ge=500, le=64000)
    min_nodes: int = Field(default=12, ge=1)
    max_nodes: int = Field(default=25, ge=1)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    timeout: float = Field(default=300.0, gt=0.0)


class FileSymbols(BaseModel):
    """Extracted structural symbols and imports for a single source file."""

    file_path: str
    language: str
    imports: list[str] = Field(default_factory=list)
    classes: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)


class RepoContext(BaseModel):
    """Token-budgeted architectural skeleton extracted from a repository."""

    repo_name: str
    root_path: str
    all_files: list[str] = Field(default_factory=list)
    tree_summary: str = ""
    manifests: dict[str, str] = Field(default_factory=dict)
    entrypoints: dict[str, str] = Field(default_factory=dict)
    ast_map: list[FileSymbols] = Field(default_factory=list)
    estimated_tokens: int = 0


class ValidationReport(BaseModel):
    """Summary of deterministic repairs applied to an LLM-synthesized graph."""

    sanitized_ids: dict[str, str] = Field(default_factory=dict)
    fuzzy_matched_paths: dict[str, str] = Field(default_factory=dict)
    stripped_paths: list[str] = Field(default_factory=list)
    removed_edges: list[tuple[str, str, str]] = Field(default_factory=list)
    removed_group_refs: int = 0


class GraphResult(BaseModel):
    """Final output returned by generate_graph()."""

    graph: ArchitectureGraph
    mermaid: str
    react_flow: dict[str, Any]
    validation: ValidationReport | None = None
    context_tokens: int = 0
