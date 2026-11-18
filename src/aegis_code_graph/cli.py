"""Command-line interface for aegis-code-graph."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from aegis_code_graph import generate_graph_sync, scan_repository
from aegis_code_graph.models import GraphConfig


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="aegis-graph",
        description="Generate Mermaid.js or React Flow architecture diagrams from a repository.",
    )
    parser.add_argument("repo_path", help="Path to the repository to analyze")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output file path (.mmd for Mermaid, .json for React Flow JSON). Defaults to stdout.",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["mermaid", "json"],
        default=None,
        help="Output format (auto-detected from -o extension, or 'mermaid' by default).",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("AEGIS_BASE_URL", "http://localhost:11434/v1"),
        help="OpenAI-compatible API base URL (default: http://localhost:11434/v1).",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AEGIS_MODEL", "qwen2.5-coder:7b"),
        help="Model name to use for synthesis (default: qwen2.5-coder:7b).",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENAI_API_KEY", "ollama"),
        help="API key for the OpenAI-compatible endpoint.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=6000,
        help="Maximum context token budget for repo scanning (default: 6000).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="LLM request timeout in seconds (default: 300).",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Only scan the repository and print the extracted RepoContext JSON without calling an LLM.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `aegis-graph` CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    config = GraphConfig(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        max_tokens_budget=args.max_tokens,
        timeout=args.timeout,
    )

    if args.scan_only:
        context = scan_repository(args.repo_path, config=config)
        payload = context.model_dump_json(indent=2)
        if args.output:
            args.output.write_text(payload + "\n", encoding="utf-8")
        else:
            print(payload)
        return 0

    fmt = args.format
    if fmt is None:
        if args.output and args.output.suffix.lower() == ".json":
            fmt = "json"
        else:
            fmt = "mermaid"

    result = generate_graph_sync(args.repo_path, config=config)
    rendered = (
        json.dumps(result.react_flow, indent=2) + "\n"
        if fmt == "json"
        else result.mermaid
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(
            f"Wrote {fmt} architecture graph ({len(result.graph.nodes)} nodes, "
            f"{len(result.graph.edges)} edges) to {args.output}",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
