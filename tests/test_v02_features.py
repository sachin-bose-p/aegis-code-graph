"""Tests for aegis-code-graph v0.2.0 features: symbol cards, import resolver, telemetry, micro-graph, and snippets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aegis_code_graph.api import read_code_snippet
from aegis_code_graph.ast_extractor import extract_symbol_card
from aegis_code_graph.compiler import compile_mermaid, compile_react_flow
from aegis_code_graph.import_resolver import resolve_imports
from aegis_code_graph.micro_extractor import extract_file_micro_graph
from aegis_code_graph.models import ArchitectureGraph, GraphEdge, GraphNode
from aegis_code_graph.scanner import scan_repository
from aegis_code_graph.models import GraphConfig
from aegis_code_graph.telemetry import compute_fan_metrics, load_security_findings
from aegis_code_graph.validator import validate_graph


def test_symbol_card_extracts_kotlin_viewmodel_and_state(tmp_path: Path) -> None:
    vm_file = tmp_path / "ContactViewModel.kt"
    vm_file.write_text(
        """
package com.example.contacts.ui

import androidx.lifecycle.ViewModel
import com.example.contacts.data.ContactRepository

data class ContactUiState(val items: List<String> = emptyList())

@HiltViewModel
class ContactViewModel(private val repo: ContactRepository) : ViewModel() {
    suspend fun loadContacts() {}
    fun deleteContact(id: String) {}
}
""",
        encoding="utf-8",
    )

    card = extract_symbol_card(vm_file, rel_path="app/src/main/java/com/example/contacts/ui/ContactViewModel.kt")
    assert card is not None
    assert card.package_name == "com.example.contacts.ui"
    assert card.primary_symbol == "ContactUiState" or "ContactViewModel" in card.classes
    assert card.symbol_type == "ViewModel"
    assert "ContactUiState" in card.state_models
    assert any("loadContacts" in m for m in card.methods)
    assert any("deleteContact" in m for m in card.methods)
    assert card.loc > 0


def test_import_resolver_matches_kotlin_dart_and_python(tmp_path: Path) -> None:
    # Kotlin pair
    kt_vm = tmp_path / "app" / "ContactViewModel.kt"
    kt_repo = tmp_path / "app" / "ContactRepository.kt"
    kt_vm.parent.mkdir(parents=True)
    kt_repo.write_text("package com.example.data\nclass ContactRepository {}\n", encoding="utf-8")
    kt_vm.write_text(
        "package com.example.ui\nimport com.example.data.ContactRepository\nclass ContactViewModel {}\n",
        encoding="utf-8",
    )

    # Dart pair
    (tmp_path / "lib" / "ui").mkdir(parents=True)
    (tmp_path / "lib" / "data").mkdir(parents=True)
    dart_screen = tmp_path / "lib" / "ui" / "home_screen.dart"
    dart_repo = tmp_path / "lib" / "data" / "auth_repo.dart"
    dart_repo.write_text("class AuthRepo {}\n", encoding="utf-8")
    dart_screen.write_text(
        "import '../data/auth_repo.dart';\nclass HomeScreen {}\n",
        encoding="utf-8",
    )

    all_files = [
        "app/ContactViewModel.kt",
        "app/ContactRepository.kt",
        "lib/ui/home_screen.dart",
        "lib/data/auth_repo.dart",
    ]
    cards = [
        c
        for c in (extract_symbol_card(tmp_path / f, rel_path=f) for f in all_files)
        if c is not None
    ]
    edges = resolve_imports(cards, all_files)

    pairs = {(e.source_file, e.target_file) for e in edges}
    assert ("app/ContactViewModel.kt", "app/ContactRepository.kt") in pairs
    assert ("lib/ui/home_screen.dart", "lib/data/auth_repo.dart") in pairs

    fan_in, fan_out = compute_fan_metrics(edges)
    assert fan_out["app/ContactViewModel.kt"] == 1
    assert fan_in["app/ContactRepository.kt"] == 1


def test_telemetry_parses_sarif_and_attaches_in_validator(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    service_file = src_dir / "auth_service.py"
    service_file.write_text("class AuthService:\n    def login(self):\n        pass\n", encoding="utf-8")

    sarif_file = tmp_path / "report.sarif"
    sarif_file.write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "tool": {"driver": {"name": "semgrep", "rules": []}},
                        "results": [
                            {
                                "ruleId": "python.lang.security.hardcoded-secret",
                                "level": "error",
                                "message": {"text": "Hardcoded API secret detected"},
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {"uri": "src/auth_service.py"},
                                            "region": {"startLine": 2},
                                        }
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    findings = load_security_findings(sarif_file, tmp_path, ["src/auth_service.py"])
    assert "src/auth_service.py" in findings
    assert findings["src/auth_service.py"][0].severity == "high"
    assert findings["src/auth_service.py"][0].line == 2

    ctx = scan_repository(tmp_path, GraphConfig(sarif_path=str(sarif_file)))
    raw = ArchitectureGraph(
        project_name="Demo",
        architecture_pattern="Layered",
        nodes=[
            GraphNode(
                id="auth_service",
                label="AuthService",
                file_path="src/auth_service.py",
                layer="domain",
                summary="Authenticates users against identity store",
            )
        ],
        edges=[],
    )
    clean, _ = validate_graph(raw, ctx.all_files, context=ctx)
    assert len(clean.nodes[0].security_flags) == 1
    assert clean.nodes[0].loc > 0

    rf = compile_react_flow(clean)
    assert rf["stats"]["security_finding_count"] == 1
    assert rf["nodes"][0]["data"]["security_flags"][0]["rule_id"] == "python.lang.security.hardcoded-secret"


def test_micro_extractor_and_code_snippet(tmp_path: Path) -> None:
    py_file = tmp_path / "repo.py"
    py_file.write_text(
        """class ContactRepository:
    def fetch_all(self):
        return self._query_db()

    def _query_db(self):
        return []
""",
        encoding="utf-8",
    )

    micro = extract_file_micro_graph(tmp_path, "repo.py")
    assert micro.class_name == "ContactRepository"
    names = {m.name for m in micro.methods}
    assert {"fetch_all", "_query_db"} <= names
    assert any(e.source == "fetch_all" and e.target == "_query_db" for e in micro.internal_edges)

    snippet = read_code_snippet(tmp_path, "repo.py", line=2, context_lines=2)
    assert "fetch_all" in snippet["content"]
    assert snippet["highlight_line"] == 2

    with pytest.raises(FileNotFoundError):
        read_code_snippet(tmp_path, "../outside.py")


def test_compile_mermaid_renders_method_subtitles_and_rich_edges() -> None:
    graph = ArchitectureGraph(
        project_name="Contacts",
        architecture_pattern="MVVM",
        nodes=[
            GraphNode(
                id="vm",
                label="ContactViewModel",
                file_path="ui/ContactViewModel.kt",
                layer="state",
                symbol_type="ViewModel",
                summary="Exposes contact UI state flow",
                key_methods=["loadContacts()", "deleteContact(id)"],
            ),
            GraphNode(
                id="repo",
                label="ContactRepository",
                file_path="data/ContactRepository.kt",
                layer="data",
                symbol_type="Repository",
                summary="Persists contacts to Room DAO",
                key_methods=["getContacts()"],
            ),
        ],
        edges=[
            GraphEdge(
                id="e_0",
                source="vm",
                target="repo",
                edge_type="method_call",
                relation="queries getContacts()",
                data_payload="Flow<List<Contact>>",
                verified=True,
            )
        ],
    )
    mmd = compile_mermaid(graph)
    assert "ContactViewModel<br/><i>loadContacts(), deleteContact()</i>" in mmd
    assert 'vm -->|"queries getContacts()"| repo' in mmd
