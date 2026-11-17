"""Tests for Mermaid.js and React Flow JSON compilers."""

from __future__ import annotations

from aegis_code_graph.compiler import compile_mermaid, compile_react_flow
from aegis_code_graph.models import ArchitectureGraph, GraphEdge, GraphNode, SubgraphGroup


def _sample_graph(*, with_groups: bool = True) -> ArchitectureGraph:
    groups = (
        [
            SubgraphGroup(id="ui_group", title="Presentation", node_ids=["login_screen"]),
            SubgraphGroup(id="data_group", title="Data", node_ids=["auth_repo", "firebase_auth"]),
        ]
        if with_groups
        else []
    )
    return ArchitectureGraph(
        project_name="SampleApp",
        architecture_pattern="Flutter BLoC",
        groups=groups,
        nodes=[
            GraphNode(
                id="login_screen",
                label='Login "Screen"',
                file_path="lib/ui/login_screen.dart",
                layer="ui",
                summary="Renders login form",
            ),
            GraphNode(
                id="auth_repo",
                label="Auth Repository",
                file_path="lib/data/auth_repo.dart",
                layer="data",
                summary="Coordinates sign-in",
            ),
            GraphNode(
                id="firebase_auth",
                label="Firebase Auth",
                file_path="",
                layer="external",
                summary="External identity provider",
            ),
        ],
        edges=[
            GraphEdge(source="login_screen", target="auth_repo", relation="submits credentials"),
            GraphEdge(source="auth_repo", target="firebase_auth", relation="authenticates via"),
        ],
    )


def test_compile_mermaid_includes_subgraphs_classes_and_clicks() -> None:
    mermaid = compile_mermaid(_sample_graph(with_groups=True))

    assert mermaid.startswith("flowchart TD\n")
    assert 'subgraph ui_group["Presentation"]' in mermaid
    assert "login_screen[\"Login 'Screen'\"]" in mermaid
    assert 'login_screen -->|"submits credentials"| auth_repo' in mermaid
    assert "classDef ui " in mermaid
    assert "class login_screen ui;" in mermaid
    assert 'click login_screen "lib/ui/login_screen.dart" "Renders login form"' in mermaid
    # External node with empty file_path should not emit a click directive
    assert "click firebase_auth" not in mermaid


def test_compile_mermaid_auto_groups_by_layer_when_groups_empty() -> None:
    mermaid = compile_mermaid(_sample_graph(with_groups=False))

    assert 'subgraph layer_ui["UI & Presentation"]' in mermaid
    assert 'subgraph layer_data["Data & Repositories"]' in mermaid
    assert 'subgraph layer_external["External Services"]' in mermaid


def test_compile_react_flow_outputs_expected_structure() -> None:
    payload = compile_react_flow(_sample_graph(with_groups=True))

    assert payload["project_name"] == "SampleApp"
    assert len(payload["nodes"]) == 3
    assert len(payload["edges"]) == 2
    assert len(payload["groups"]) == 2

    first_node = payload["nodes"][0]
    assert first_node["id"] == "login_screen"
    assert first_node["type"] == "architectureNode"
    assert first_node["data"]["file_path"] == "lib/ui/login_screen.dart"
    assert first_node["data"]["group_id"] == "ui_group"
    assert "x" in first_node["position"] and "y" in first_node["position"]
