"""Tests for deterministic path, ID, and edge validation."""

from __future__ import annotations

from aegis_code_graph.models import ArchitectureGraph, GraphEdge, GraphNode, SubgraphGroup
from aegis_code_graph.validator import sanitize_id, validate_graph


def test_sanitize_id_handles_special_chars_digits_and_reserved() -> None:
    used: set[str] = set()
    assert sanitize_id("auth-service.v1", used) == "auth_service_v1"
    assert sanitize_id("123_node", used) == "n_123_node"
    assert sanitize_id("end", used) == "end_node"
    assert sanitize_id("auth-service.v1", used) == "auth_service_v1_2"


def test_validate_graph_verifies_paths_and_sanitizes_orphans() -> None:
    raw = ArchitectureGraph(
        project_name="DemoApp",
        architecture_pattern="Clean Architecture",
        groups=[
            SubgraphGroup(
                id="ui-layer",
                title="UI Layer",
                node_ids=["home-screen", "ghost_node"],
            )
        ],
        nodes=[
            GraphNode(
                id="home-screen",
                label="Home Screen",
                file_path="./lib/ui/home_screen.dart",
                layer="ui",
                summary="Main screen",
            ),
            GraphNode(
                id="auth_repo",
                label="Auth Repository",
                file_path="lib/auth_repository.dart",  # slightly off path -> fuzzy match
                layer="data",
                summary="Handles auth",
            ),
            GraphNode(
                id="hallucinated",
                label="Fake Service",
                file_path="lib/completely/nonexistent/foo_bar_baz.dart",
                layer="domain",
                summary="Does not exist",
            ),
        ],
        edges=[
            GraphEdge(source="home-screen", target="auth_repo", relation="calls"),
            GraphEdge(source="home-screen", target="missing_target", relation="broken"),
        ],
    )

    valid_files = [
        "lib/ui/home_screen.dart",
        "lib/data/auth_repository.dart",
        "pubspec.yaml",
    ]

    clean, report = validate_graph(raw, valid_files)

    node_map = {n.id: n for n in clean.nodes}
    assert "home_screen" in node_map
    assert node_map["home_screen"].file_path == "lib/ui/home_screen.dart"
    assert node_map["auth_repo"].file_path == "lib/data/auth_repository.dart"
    assert node_map["hallucinated"].file_path == ""

    assert report.sanitized_ids == {"home-screen": "home_screen"}
    assert "lib/auth_repository.dart" in report.fuzzy_matched_paths
    assert "hallucinated" in report.stripped_paths
    assert len(report.removed_edges) == 1
    assert report.removed_group_refs == 1

    assert len(clean.edges) == 1
    assert clean.edges[0].source == "home_screen"
    assert clean.edges[0].target == "auth_repo"
    assert clean.groups[0].node_ids == ["home_screen"]
