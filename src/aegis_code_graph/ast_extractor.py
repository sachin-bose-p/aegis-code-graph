"""Lightweight AST symbol and import extractor using Tree-sitter with regex fallback."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from aegis_code_graph.models import FileSymbols

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".dart": "dart",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}

_IMPORT_NODE_TYPES: frozenset[str] = frozenset(
    {
        "import_or_export",
        "import_statement",
        "import_from_statement",
        "import_declaration",
        "import_header",
    }
)

_CLASS_NODE_TYPES: frozenset[str] = frozenset(
    {
        "class_definition",
        "class_declaration",
        "struct_declaration",
        "protocol_declaration",
        "interface_declaration",
        "mixin_declaration",
        "enum_declaration",
        "object_declaration",
    }
)

_FUNCTION_NODE_TYPES: frozenset[str] = frozenset(
    {
        "function_definition",
        "function_declaration",
        "function_signature",
    }
)

_IDENTIFIER_NODE_TYPES: frozenset[str] = frozenset(
    {
        "identifier",
        "type_identifier",
        "simple_identifier",
    }
)


def is_supported_source(file_path: str | Path) -> bool:
    """Return True if the file extension maps to a supported AST language."""
    return Path(file_path).suffix.lower() in EXTENSION_TO_LANGUAGE


def detect_language(file_path: str | Path) -> str | None:
    """Detect Tree-sitter language name from a file extension."""
    return EXTENSION_TO_LANGUAGE.get(Path(file_path).suffix.lower())


def _clean_import_text(raw: str) -> str:
    """Normalize an import statement into a compact, readable reference."""
    line = " ".join(raw.strip().split())
    line = line.rstrip(";")
    return line


def _extract_identifier(node: Any) -> str | None:
    """Extract the declared identifier name from a class or function AST node."""
    name_node = node.child_by_field_name("name")
    if name_node is not None and name_node.text:
        return name_node.text.decode("utf-8", errors="ignore")

    for child in node.children:
        if child.type in _IDENTIFIER_NODE_TYPES and child.text:
            return child.text.decode("utf-8", errors="ignore")
    return None


def _extract_with_tree_sitter(source_bytes: bytes, language: str, rel_path: str) -> FileSymbols:
    """Parse source_bytes with tree-sitter-language-pack and extract symbols."""
    import tree_sitter_language_pack as tslp

    parser = tslp.get_parser(language)
    tree = parser.parse(source_bytes)

    imports: list[str] = []
    classes: list[str] = []
    functions: list[str] = []

    def visit_top_level(node: Any) -> None:
        ntype = node.type

        if ntype == "import_list":
            for child in node.children:
                visit_top_level(child)
            return

        if ntype in _IMPORT_NODE_TYPES:
            if node.text:
                cleaned = _clean_import_text(node.text.decode("utf-8", errors="ignore"))
                if cleaned and cleaned not in imports:
                    imports.append(cleaned)
            return

        if ntype == "export_statement":
            for child in node.children:
                visit_top_level(child)
            return

        if ntype in _CLASS_NODE_TYPES:
            name = _extract_identifier(node)
            if name and name not in classes:
                classes.append(name)
            return

        if ntype in _FUNCTION_NODE_TYPES:
            name = _extract_identifier(node)
            if name and name not in functions:
                functions.append(name)
            return

        # Handle JS/TS arrow-function components: `export const HomeScreen = () => ...`
        if ntype in {"lexical_declaration", "variable_declaration"}:
            for child in node.children:
                if child.type == "variable_declarator":
                    var_name = _extract_identifier(child)
                    has_fn = any(
                        gc.type in {"arrow_function", "function_expression", "function"}
                        for gc in child.children
                    )
                    if var_name and has_fn:
                        target = classes if var_name[:1].isupper() else functions
                        if var_name not in target:
                            target.append(var_name)

    for child in tree.root_node.children:
        visit_top_level(child)

    return FileSymbols(
        file_path=rel_path,
        language=language,
        imports=imports[:30],
        classes=classes[:25],
        functions=functions[:15],
    )


_REGEX_IMPORT = re.compile(
    r"^\s*(?:import\s+[^\n;]+|from\s+[^\n]+\s+import\s+[^\n]+)",
    re.MULTILINE,
)
_REGEX_CLASS = re.compile(
    r"^\s*(?:export\s+)?(?:abstract\s+|final\s+|sealed\s+|data\s+|open\s+|public\s+|private\s+|internal\s+)*"
    r"(?:class|struct|protocol|interface|enum|mixin|object)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
_REGEX_FUNC = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:def|fun|func|function)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)


def _extract_with_regex(source_text: str, language: str, rel_path: str) -> FileSymbols:
    """Fallback regex extractor when Tree-sitter parsing is unavailable."""
    imports = [_clean_import_text(m.group(0)) for m in _REGEX_IMPORT.finditer(source_text)]
    classes = list(dict.fromkeys(m.group(1) for m in _REGEX_CLASS.finditer(source_text)))
    functions = list(dict.fromkeys(m.group(1) for m in _REGEX_FUNC.finditer(source_text)))

    return FileSymbols(
        file_path=rel_path,
        language=language,
        imports=list(dict.fromkeys(imports))[:30],
        classes=classes[:25],
        functions=functions[:15],
    )


def extract_file_symbols(
    file_path: str | Path,
    rel_path: str | None = None,
) -> FileSymbols | None:
    """Extract imports, classes, and top-level functions from a source file.

    Uses Tree-sitter by default and falls back to regex if parsing fails.
    Returns None if the file extension is not supported or cannot be read.
    """
    path = Path(file_path)
    language = detect_language(path)
    if language is None:
        return None

    display_path = rel_path or path.as_posix()
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return None

    try:
        return _extract_with_tree_sitter(raw_bytes, language, display_path)
    except Exception:
        text = raw_bytes.decode("utf-8", errors="ignore")
        return _extract_with_regex(text, language, display_path)
