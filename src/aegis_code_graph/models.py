"""Pydantic v2 schemas for aegis-code-graph (v0.2.0)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

LayerType = Literal["ui", "state", "domain", "data", "infra", "external"]
EdgeType = Literal["state_stream", "method_call", "data_io", "navigation"]
SeverityType = Literal["critical", "high", "medium", "low"]


class SecurityFlag(BaseModel):
    """AppSec finding attached to a source file / architectural node."""

    rule_id: str
    severity: SeverityType
    line: int = 0
    message: str


class GraphNode(BaseModel):
    """A single architectural component in the codebase."""

    id: str = Field(..., description="Alphanumeric snake_case ID, e.g., contact_repository")
    label: str = Field(..., description="Exact class/file symbol, e.g., ContactRepository")
    file_path: str = Field(..., description="Exact relative path from repo root")
    layer: LayerType = Field(..., description="Architectural layer")
    symbol_type: str = Field(
        default="Module",
        description="Framework stereotype: Composable, ViewModel, Repository, DAO, APIClient, etc.",
    )
    summary: str = Field(..., description="Concrete responsibility (never repeats label)")
    key_methods: list[str] = Field(default_factory=list, description="Top 3-5 public methods/endpoints")
    state_models: list[str] = Field(default_factory=list, description="DTOs, Entities, UI State classes")
    loc: int = 0
    fan_in: int = 0
    fan_out: int = 0
    security_flags: list[SecurityFlag] = Field(default_factory=list)


class GraphEdge(BaseModel):
    """A directed dependency or interaction between two components."""

    id: str = Field(default="", description="Unique edge ID")
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    edge_type: EdgeType = Field(
        default="method_call",
        description="Semantic edge category: state_stream, method_call, data_io, or navigation",
    )
    relation: str = Field(
        ...,
        description="Concrete verb/method phrase, e.g., 'collects uiState' or 'queries getAllContacts()'",
    )
    data_payload: str | None = Field(default=None, description="e.g., Flow<List<Contact>>")
    verified: bool = Field(default=False, description="True if backed by AST import/call")


class SubgraphGroup(BaseModel):
    """A logical grouping of architectural nodes."""

    id: str = Field(..., description="Alphanumeric snake_case group ID")
    title: str = Field(..., description="Human-readable group title")
    node_ids: list[str] = Field(default_factory=list, description="Member node IDs")


class ArchitectureGraph(BaseModel):
    """Complete synthesized architecture graph."""

    project_name: str
    architecture_pattern: str = Field(
        ..., description="e.g., 'Android MVVM Clean Architecture'"
    )
    groups: list[SubgraphGroup] = Field(default_factory=list)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class MicroMethodInfo(BaseModel):
    """Method-level detail extracted for micro-graph drilldown."""

    name: str
    signature: str
    visibility: str = "public"
    line: int = 0
    calls: list[str] = Field(default_factory=list)


class MicroMethodGraph(BaseModel):
    """Pure Tree-sitter intra-file method and call graph."""

    file_path: str
    class_name: str
    language: str = ""
    methods: list[MicroMethodInfo] = Field(default_factory=list)
    state_properties: list[str] = Field(default_factory=list)
    internal_edges: list[GraphEdge] = Field(default_factory=list)


class GraphConfig(BaseModel):
    """Configuration for scanning, LLM synthesis, telemetry, and compilation."""

    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    model: str = "qwen2.5-coder:7b"
    max_tokens_budget: int = Field(default=6000, ge=1000, le=128000)
    ast_token_budget: int = Field(default=3000, ge=500, le=64000)
    min_nodes: int = Field(default=12, ge=1)
    max_nodes: int = Field(default=25, ge=1)
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    timeout: float = Field(default=300.0, gt=0.0)
    sarif_path: str | None = Field(
        default=None,
        description="Optional path to SARIF/mobsfscan/semgrep JSON report for security overlay",
    )


class FileSymbols(BaseModel):
    """Extracted structural symbols and imports for a single source file."""

    file_path: str
    language: str
    imports: list[str] = Field(default_factory=list)
    classes: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)


class FileSymbolCard(FileSymbols):
    """Rich v0.2 symbol card extracted deterministically via Tree-sitter."""

    package_name: str = ""
    primary_symbol: str = ""
    symbol_type: str = "Module"
    methods: list[str] = Field(default_factory=list)
    state_models: list[str] = Field(default_factory=list)
    loc: int = 0


class VerifiedCallEdge(BaseModel):
    """Deterministic file-to-file dependency resolved from imports and symbols."""

    source_file: str
    target_file: str
    matched_symbols: list[str] = Field(default_factory=list)


class RepoContext(BaseModel):
    """Token-budgeted architectural skeleton extracted from a repository."""

    repo_name: str
    root_path: str
    all_files: list[str] = Field(default_factory=list)
    tree_summary: str = ""
    manifests: dict[str, str] = Field(default_factory=dict)
    entrypoints: dict[str, str] = Field(default_factory=dict)
    ast_map: list[FileSymbols] = Field(default_factory=list)
    symbol_cards: list[FileSymbolCard] = Field(default_factory=list)
    verified_edges: list[VerifiedCallEdge] = Field(default_factory=list)
    sarif_findings: dict[str, list[SecurityFlag]] = Field(default_factory=dict)
    estimated_tokens: int = 0


class ValidationReport(BaseModel):
    """Summary of deterministic repairs applied to an LLM-synthesized graph."""

    sanitized_ids: dict[str, str] = Field(default_factory=dict)
    fuzzy_matched_paths: dict[str, str] = Field(default_factory=dict)
    stripped_paths: list[str] = Field(default_factory=list)
    removed_edges: list[tuple[str, str, str]] = Field(default_factory=list)
    removed_group_refs: int = 0
    verified_edge_count: int = 0


class GraphResult(BaseModel):
    """Final output returned by generate_graph()."""

    graph: ArchitectureGraph
    mermaid: str
    react_flow: dict[str, Any]
    validation: ValidationReport | None = None
    context_tokens: int = 0
