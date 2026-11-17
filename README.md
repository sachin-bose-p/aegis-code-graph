# aegis-code-graph

Zero-bloat, local-first, deterministic-plus-LLM hybrid repository-to-architecture diagram engine.

Extracts a compact, token-budgeted architectural skeleton (`.gitignore`-aware file tree, mobile/app manifests, entrypoints, and Tree-sitter AST imports/classes), synthesizes a validated `ArchitectureGraph` via any OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, or cloud APIs), and compiles deterministic **Mermaid.js** and **React Flow** JSON payloads.

## Installation

```bash
pip install aegis-code-graph
```

## Quick Start (Python)

```python
from aegis_code_graph import GraphConfig, generate_graph_sync

result = generate_graph_sync(
    "./my-app",
    config=GraphConfig(
        base_url="http://localhost:11434/v1",
        model="qwen2.5-coder:7b",
    ),
)

print(result.mermaid)
print(result.react_flow)
```

## CLI Usage

```bash
# Generate Mermaid.js flowchart (default)
aegis-graph ./my-app -o arch.mmd

# Generate React Flow JSON
aegis-graph ./my-app -o arch.json

# Inspect scanned repository skeleton without calling an LLM
aegis-graph ./my-app --scan-only
```

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```
