"""Gitignore-aware repository scanner and token-budgeted context builder."""

from __future__ import annotations

import os
from pathlib import Path

import pathspec

from aegis_code_graph.ast_extractor import extract_file_symbols, is_supported_source
from aegis_code_graph.models import FileSymbols, GraphConfig, RepoContext

DEFAULT_IGNORED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".dart_tool",
        "build",
        "Pods",
        ".gradle",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        "dist",
        "DerivedData",
        ".next",
        ".nuxt",
        "coverage",
        "target",
        "META-INF",
        "smali",
        "smali_classes2",
        "smali_classes3",
        "smali_classes4",
        "smali_classes5",
        "androidx",
    }
)

# Subpath prefixes (POSIX, lowercase) inside decompiled APKs / vendor trees to skip
DEFAULT_IGNORED_SUBPATHS: tuple[str, ...] = (
    "/android/support/",
    "/com/google/android/",
    "/com/google/gson/",
    "/kotlin/",
    "/kotlinx/",
    "/okhttp3/",
    "/retrofit2/",
    "/res/drawable",
    "/res/mipmap",
    "/res/values",
    "/res/color",
    "/res/anim",
)

DEFAULT_IGNORED_FILES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "pubspec.lock",
        "Podfile.lock",
        "poetry.lock",
        "uv.lock",
        "Cargo.lock",
        ".DS_Store",
    }
)

BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".svg",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".jar",
        ".aar",
        ".apk",
        ".ipa",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".wasm",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".mp3",
        ".mp4",
        ".mov",
        ".db",
        ".sqlite",
        ".pyc",
        ".pyo",
    }
)

MANIFEST_FILENAMES: frozenset[str] = frozenset(
    {
        "README.md",
        "pubspec.yaml",
        "AndroidManifest.xml",
        "build.gradle",
        "build.gradle.kts",
        "Podfile",
        "Info.plist",
        "package.json",
        "pyproject.toml",
        "Cargo.toml",
        "Package.swift",
    }
)

ENTRYPOINT_FILENAMES: frozenset[str] = frozenset(
    {
        "main.dart",
        "App.tsx",
        "App.jsx",
        "App.js",
        "index.tsx",
        "index.ts",
        "MainActivity.kt",
        "MainActivity.java",
        "AppDelegate.swift",
        "SceneDelegate.swift",
        "main.py",
        "app.py",
        "__main__.py",
    }
)


def estimate_tokens(text: str) -> int:
    """Estimate token count using a lightweight 4-chars-per-token heuristic."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _load_gitignore_spec(root: Path) -> pathspec.PathSpec:
    """Load .gitignore patterns from the repository root if present."""
    gitignore_path = root / ".gitignore"
    lines: list[str] = []
    if gitignore_path.is_file():
        try:
            lines = gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            lines = []
    return pathspec.PathSpec.from_lines("gitignore", lines)


def _is_ignored(rel_posix: str, name: str, is_dir: bool, spec: pathspec.PathSpec) -> bool:
    """Return True if a file or directory should be excluded from scanning."""
    normalized = f"/{rel_posix.lower()}"
    if any(sub in normalized for sub in DEFAULT_IGNORED_SUBPATHS):
        return True

    if is_dir:
        if name in DEFAULT_IGNORED_DIRS or name.startswith("smali"):
            return True
        return spec.match_file(f"{rel_posix}/") or spec.match_file(rel_posix)

    if name in DEFAULT_IGNORED_FILES:
        return True
    if Path(name).suffix.lower() in BINARY_EXTENSIONS:
        return True
    return spec.match_file(rel_posix)


def walk_repository(root: Path) -> list[str]:
    """Walk a repository respecting .gitignore and default noise filters.

    Returns sorted relative POSIX paths of all non-ignored files.
    """
    root = root.resolve()
    spec = _load_gitignore_spec(root)
    collected: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        rel_dir = current.relative_to(root)

        # Prune ignored directories in-place so os.walk does not descend into them.
        kept_dirs: list[str] = []
        for d in sorted(dirnames):
            rel_posix = (rel_dir / d).as_posix() if rel_dir != Path(".") else d
            if not _is_ignored(rel_posix, d, is_dir=True, spec=spec):
                kept_dirs.append(d)
        dirnames[:] = kept_dirs

        for f in sorted(filenames):
            rel_posix = (rel_dir / f).as_posix() if rel_dir != Path(".") else f
            if not _is_ignored(rel_posix, f, is_dir=False, spec=spec):
                collected.append(rel_posix)

    return sorted(collected)


def _read_truncated(path: Path, max_chars: int = 4000) -> str:
    """Read a text file up to max_chars, appending a truncation marker if needed."""
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n... [truncated]"


def _prioritize_source_files(files: list[str]) -> list[str]:
    """Sort source files so core app directories and shallower files come first."""
    test_dirs = {"test", "tests", "__tests__", "spec", "unittest", "androidtest"}

    def sort_key(rel_path: str) -> tuple[int, int, int, str]:
        parts = rel_path.lower().split("/")
        name = parts[-1] if parts else ""
        in_core = (
            0
            if any(p in {"lib", "src", "app", "sources", "packages", "java", "kotlin"} for p in parts[:-1])
            else 1
        )
        is_test = (
            1
            if (
                any(p in test_dirs for p in parts[:-1])
                or name.startswith("test_")
                or name.endswith(("_test.dart", "test.kt", "test.java", ".test.ts", ".test.tsx", ".spec.ts"))
            )
            else 0
        )
        return (is_test, in_core, len(parts), rel_path)

    return sorted((f for f in files if is_supported_source(f)), key=sort_key)


def scan_repository(
    repo_path: str | Path,
    config: GraphConfig | None = None,
) -> RepoContext:
    """Scan a repository and build a token-budgeted architectural skeleton.

    Args:
        repo_path: Path to the target repository root.
        config: Optional GraphConfig controlling token budgets.

    Returns:
        RepoContext containing file list, tree summary, manifests, entrypoints,
        and AST symbol map within the configured token budget.
    """
    cfg = config or GraphConfig()
    root = Path(repo_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Repository path does not exist or is not a directory: {root}")

    all_files = walk_repository(root)
    used_tokens = 0

    # 1. Directory tree summary (reserve up to ~25% of total budget)
    tree_budget = max(500, cfg.max_tokens_budget // 4)
    tree_lines: list[str] = []
    tree_tokens = 0
    for rel_path in all_files:
        line_tokens = estimate_tokens(rel_path + "\n")
        if tree_tokens + line_tokens > tree_budget:
            remaining = len(all_files) - len(tree_lines)
            tree_lines.append(f"... ({remaining} more files)")
            break
        tree_lines.append(rel_path)
        tree_tokens += line_tokens
    tree_summary = "\n".join(tree_lines)
    used_tokens += tree_tokens

    # 2. High-signal manifests & entrypoints
    manifests: dict[str, str] = {}
    entrypoints: dict[str, str] = {}
    high_signal_budget = max(1000, cfg.max_tokens_budget - cfg.ast_token_budget - used_tokens)
    high_signal_tokens = 0

    for rel_path in all_files:
        name = Path(rel_path).name
        is_manifest = name in MANIFEST_FILENAMES
        is_entrypoint = name in ENTRYPOINT_FILENAMES
        if not (is_manifest or is_entrypoint):
            continue

        content = _read_truncated(root / rel_path, max_chars=3500)
        if not content.strip():
            continue
        tok = estimate_tokens(content)
        if high_signal_tokens + tok > high_signal_budget:
            continue

        if is_manifest:
            manifests[rel_path] = content
        else:
            entrypoints[rel_path] = content
        high_signal_tokens += tok

    used_tokens += high_signal_tokens

    # 3. AST symbol & import extraction within ast_token_budget
    remaining_budget = max(0, cfg.max_tokens_budget - used_tokens)
    ast_budget = min(cfg.ast_token_budget, remaining_budget)
    ast_map: list[FileSymbols] = []
    ast_tokens = 0

    for rel_path in _prioritize_source_files(all_files):
        symbols = extract_file_symbols(root / rel_path, rel_path=rel_path)
        if symbols is None:
            continue
        if not symbols.imports and not symbols.classes and not symbols.functions:
            continue

        serialized = symbols.model_dump_json()
        tok = estimate_tokens(serialized)
        if ast_tokens + tok > ast_budget:
            break
        ast_map.append(symbols)
        ast_tokens += tok

    used_tokens += ast_tokens

    return RepoContext(
        repo_name=root.name,
        root_path=str(root),
        all_files=all_files,
        tree_summary=tree_summary,
        manifests=manifests,
        entrypoints=entrypoints,
        ast_map=ast_map,
        estimated_tokens=used_tokens,
    )
