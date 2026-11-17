"""End-to-end pipeline and CLI tests using a mocked OpenAI-compatible client."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis_code_graph import generate_graph
from aegis_code_graph.cli import main as cli_main
from aegis_code_graph.models import GraphConfig


class _FakeCompletions:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def create(self, **kwargs):  # noqa: ANN003, ANN201
        content = json.dumps(self._payload)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


class _FakeAsyncClient:
    def __init__(self, payload: dict) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(payload))


@pytest.mark.asyncio
async def test_generate_graph_end_to_end(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "mini-api"\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        "from src.service import AuthService\n\ndef run(): pass\n", encoding="utf-8"
    )
    (tmp_path / "src" / "service.py").write_text(
        "class AuthService:\n    pass\n", encoding="utf-8"
    )

    fake_graph_json = {
        "project_name": "mini-api",
        "architecture_pattern": "Layered Service",
        "groups": [
            {"id": "api", "title": "API", "node_ids": ["main_entry", "auth_service"]},
        ],
        "nodes": [
            {
                "id": "main_entry",
                "label": "Main Entrypoint",
                "file_path": "src/main.py",
                "layer": "ui",
                "summary": "Bootstraps application",
            },
            {
                "id": "auth_service",
                "label": "Auth Service",
                "file_path": "src/service.py",
                "layer": "domain",
                "summary": "Handles authentication",
            },
        ],
        "edges": [
            {"source": "main_entry", "target": "auth_service", "relation": "invokes"}
        ],
    }

    result = await generate_graph(
        tmp_path,
        config=GraphConfig(min_nodes=2, max_nodes=10),
        client=_FakeAsyncClient(fake_graph_json),
    )

    assert result.graph.project_name == "mini-api"
    assert "flowchart TD" in result.mermaid
    assert len(result.react_flow["nodes"]) == 2
    assert result.context_tokens > 0


def test_cli_scan_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "README.md").write_text("# Hello\n", encoding="utf-8")
    exit_code = cli_main([str(tmp_path), "--scan-only"])
    assert exit_code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "README.md" in data["manifests"]
