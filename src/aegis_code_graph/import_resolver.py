"""Deterministic local import-to-file resolver across Kotlin, Java, Dart, TS/JS, and Python."""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from pathlib import Path

from aegis_code_graph.models import FileSymbolCard, VerifiedCallEdge

_QUOTED_PATH_RE = re.compile(r"""['"]([^'"]+)['"]""")
_PY_FROM_RE = re.compile(r"^\s*from\s+([A-Za-z0-9_.]+)\s+import\s+(.+)$")
_PY_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z0-9_.]+)")
_JVM_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([A-Za-z0-9_.*]+)")

_TS_EXTENSIONS: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


class _SymbolIndex:
    """Lookup tables built from extracted FileSymbolCards."""

    def __init__(self, cards: list[FileSymbolCard], all_files: list[str]) -> None:
        self.all_file_set = set(all_files)
        self.fqn_to_file: dict[str, str] = {}
        self.pkg_to_files: dict[str, list[str]] = defaultdict(list)
        self.symbol_to_files: dict[str, list[str]] = defaultdict(list)

        for card in cards:
            for cls in card.classes or ([card.primary_symbol] if card.primary_symbol else []):
                self.symbol_to_files[cls].append(card.file_path)
                if card.package_name:
                    self.fqn_to_file[f"{card.package_name}.{cls}"] = card.file_path
            if card.package_name:
                self.pkg_to_files[card.package_name].append(card.file_path)


def resolve_imports(
    cards: list[FileSymbolCard],
    all_files: list[str],
) -> list[VerifiedCallEdge]:
    """Resolve local imports in FileSymbolCards to exact repository file paths.

    Returns deduplicated VerifiedCallEdge pairs (source_file -> target_file).
    """
    index = _SymbolIndex(cards, all_files)
    edges_map: dict[tuple[str, str], set[str]] = defaultdict(set)

    def _add_edge(src: str, tgt: str, symbol: str) -> None:
        if tgt and tgt != src:
            edges_map[(src, tgt)].add(symbol)

    for card in cards:
        src = card.file_path
        src_dir = posixpath.dirname(src)

        for imp in card.imports:
            # 1. Dart / TS / JS quoted import paths
            quoted = _QUOTED_PATH_RE.search(imp)
            if quoted:
                spec = quoted.group(1)
                if spec.startswith("package:"):
                    # e.g. package:my_app/data/contact_repo.dart -> lib/data/contact_repo.dart
                    parts = spec[len("package:") :].split("/", 1)
                    if len(parts) == 2:
                        candidate = f"lib/{parts[1]}"
                        if candidate in index.all_file_set:
                            _add_edge(src, candidate, Path(candidate).stem)
                elif spec.startswith("."):
                    joined = posixpath.normpath(posixpath.join(src_dir, spec))
                    exts = (".dart",) if card.language == "dart" else _TS_EXTENSIONS
                    candidates = [joined]
                    if not Path(joined).suffix:
                        candidates.extend(f"{joined}{ext}" for ext in exts)
                        candidates.extend(f"{joined}/index{ext}" for ext in exts)
                    for c in candidates:
                        if c in index.all_file_set:
                            _add_edge(src, c, Path(c).stem)
                            break
                continue

            # 2. Python imports
            if card.language == "python":
                m_from = _PY_FROM_RE.match(imp)
                if m_from:
                    mod_path = m_from.group(1).replace(".", "/")
                    syms = [s.strip().split(" as ")[0] for s in m_from.group(2).split(",")]
                    for prefix in ("", "src/"):
                        for suffix in (".py", "/__init__.py"):
                            cand = f"{prefix}{mod_path}{suffix}"
                            if cand in index.all_file_set:
                                for s in syms:
                                    if s:
                                        _add_edge(src, cand, s)
                                break
                    continue
                m_imp = _PY_IMPORT_RE.match(imp)
                if m_imp:
                    mod_path = m_imp.group(1).replace(".", "/")
                    for prefix in ("", "src/"):
                        for suffix in (".py", "/__init__.py"):
                            cand = f"{prefix}{mod_path}{suffix}"
                            if cand in index.all_file_set:
                                _add_edge(src, cand, Path(cand).stem)
                                break
                continue

            # 3. Kotlin / Java package imports
            m_jvm = _JVM_IMPORT_RE.match(imp)
            if m_jvm:
                fqn = m_jvm.group(1)
                if fqn.endswith(".*"):
                    pkg = fqn[:-2]
                    for tgt in index.pkg_to_files.get(pkg, []):
                        _add_edge(src, tgt, pkg.rsplit(".", 1)[-1])
                elif fqn in index.fqn_to_file:
                    _add_edge(src, index.fqn_to_file[fqn], fqn.rsplit(".", 1)[-1])
                else:
                    # Try matching class name if package prefix is slightly different
                    cls_name = fqn.rsplit(".", 1)[-1]
                    matches = index.symbol_to_files.get(cls_name, [])
                    if len(matches) == 1:
                        _add_edge(src, matches[0], cls_name)

    return [
        VerifiedCallEdge(
            source_file=src,
            target_file=tgt,
            matched_symbols=sorted(syms),
        )
        for (src, tgt), syms in sorted(edges_map.items())
    ]
