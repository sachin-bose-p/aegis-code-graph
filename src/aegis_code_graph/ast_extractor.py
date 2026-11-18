"""Lightweight AST symbol card and import extractor using Tree-sitter with regex fallback."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from aegis_code_graph.models import FileSymbolCard

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

_PACKAGE_NODE_TYPES: frozenset[str] = frozenset(
    {
        "package_header",
        "package_declaration",
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
        "method_declaration",
    }
)

_IDENTIFIER_NODE_TYPES: frozenset[str] = frozenset(
    {
        "identifier",
        "type_identifier",
        "simple_identifier",
    }
)

_STATE_MODEL_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9]*(?:UiState|State|Entity|Dto|DTO|Model|Event|Intent|Response|Request))\b"
)
_METHOD_EXCLUDE: frozenset[str] = frozenset({"__init__", "build", "toString", "hashCode", "equals"})


def is_supported_source(file_path: str | Path) -> bool:
    """Return True if the file extension maps to a supported AST language."""
    return Path(file_path).suffix.lower() in EXTENSION_TO_LANGUAGE


def detect_language(file_path: str | Path) -> str | None:
    """Detect Tree-sitter language name from a file extension."""
    return EXTENSION_TO_LANGUAGE.get(Path(file_path).suffix.lower())


def _clean_import_text(raw: str) -> str:
    """Normalize an import statement into a compact, readable reference."""
    line = " ".join(raw.strip().split())
    return line.rstrip(";")


def _clean_package_text(raw: str) -> str:
    """Extract dotted package name from a package declaration."""
    text = " ".join(raw.strip().split()).rstrip(";")
    if text.startswith("package "):
        return text[len("package ") :].strip()
    return text


def _extract_identifier(node: Any) -> str | None:
    """Extract the declared identifier name from a class or function AST node."""
    name_node = node.child_by_field_name("name")
    if name_node is not None and name_node.text:
        return name_node.text.decode("utf-8", errors="ignore")

    for child in node.children:
        if child.type in _IDENTIFIER_NODE_TYPES and child.text:
            return child.text.decode("utf-8", errors="ignore")
    return None


def _extract_method_signature(node: Any) -> str | None:
    """Extract a compact one-line method signature without its body."""
    if not node.text:
        return None
    raw = node.text.decode("utf-8", errors="ignore")
    header = raw.split("{", 1)[0].split("=>", 1)[0]
    if ":" in header and header.rstrip().endswith(":"):
        header = header.rstrip()[:-1]
    compact = " ".join(header.split()).strip()
    if len(compact) > 90:
        compact = compact[:87] + "..."
    return compact or None


def _infer_symbol_type(
    source_text: str,
    primary_symbol: str,
    rel_path: str,
) -> str:
    """Infer architectural stereotype from annotations, supertypes, and naming."""
    checks: list[tuple[str, str]] = [
        ("@Composable", "Composable"),
        ("@HiltViewModel", "ViewModel"),
        ("ViewModel", "ViewModel"),
        ("AndroidViewModel", "ViewModel"),
        ("StatefulWidget", "StatefulWidget"),
        ("StatelessWidget", "StatelessWidget"),
        ("Bloc<", "Bloc"),
        ("Cubit<", "Cubit"),
        ("ChangeNotifier", "ChangeNotifier"),
        ("@Dao", "DAO"),
        ("@Entity", "Entity"),
        ("@RestController", "Controller"),
        ("@Service", "Service"),
        ("@Repository", "Repository"),
        ("Retrofit", "APIClient"),
    ]
    for marker, stype in checks:
        if marker in source_text:
            return stype

    name_lower = (primary_symbol or Path(rel_path).stem).lower()
    suffix_map: tuple[tuple[str, str], ...] = (
        ("viewmodel", "ViewModel"),
        ("repository", "Repository"),
        ("repo", "Repository"),
        ("usecase", "UseCase"),
        ("interactor", "UseCase"),
        ("dao", "DAO"),
        ("api", "APIClient"),
        ("client", "APIClient"),
        ("service", "Service"),
        ("controller", "Controller"),
        ("screen", "Screen"),
        ("activity", "Activity"),
        ("fragment", "Fragment"),
        ("bloc", "Bloc"),
        ("cubit", "Cubit"),
    )
    for suffix, stype in suffix_map:
        if suffix in name_lower:
            return stype
    return "Module"


def _extract_methods_from_body(class_node: Any, methods: list[str]) -> None:
    """Extract method signatures from a class/struct body without descending into method bodies."""
    for child in class_node.children:
        if child.type in {"class_body", "declaration_list", "block", "suite"}:
            for member in child.children:
                if member.type in _FUNCTION_NODE_TYPES:
                    m_name = _extract_identifier(member)
                    if m_name and m_name not in _METHOD_EXCLUDE and not m_name.startswith("_"):
                        sig = _extract_method_signature(member)
                        if sig and sig not in methods:
                            methods.append(sig)
                elif member.type in {"decorated_definition", "function_definition"}:
                    sig = _extract_method_signature(member)
                    m_name = _extract_identifier(member)
                    if sig and m_name and m_name not in _METHOD_EXCLUDE and sig not in methods:
                        methods.append(sig)


def _extract_with_tree_sitter(source_bytes: bytes, language: str, rel_path: str) -> FileSymbolCard:
    """Parse source_bytes with tree-sitter-language-pack and extract a FileSymbolCard."""
    import tree_sitter_language_pack as tslp

    parser = tslp.get_parser(language)
    tree = parser.parse(source_bytes)
    source_text = source_bytes.decode("utf-8", errors="ignore")

    package_name = ""
    imports: list[str] = []
    classes: list[str] = []
    functions: list[str] = []
    methods: list[str] = []
    state_models: list[str] = []

    def visit_top_level(node: Any) -> None:
        nonlocal package_name
        ntype = node.type

        if ntype in _PACKAGE_NODE_TYPES and node.text and not package_name:
            package_name = _clean_package_text(node.text.decode("utf-8", errors="ignore"))
            return

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

        if ntype in {"export_statement", "decorated_definition"}:
            for child in node.children:
                visit_top_level(child)
            return

        if ntype in _CLASS_NODE_TYPES:
            name = _extract_identifier(node)
            if name and name not in classes:
                classes.append(name)
            node_text = node.text.decode("utf-8", errors="ignore") if node.text else ""
            if any(kw in node_text[:80] for kw in ("data class", "sealed ", "enum class")):
                if name and name not in state_models:
                    state_models.append(name)
            _extract_methods_from_body(node, methods)
            return

        if ntype in _FUNCTION_NODE_TYPES:
            name = _extract_identifier(node)
            if name and name not in functions:
                functions.append(name)
            sig = _extract_method_signature(node)
            if sig and name and not name.startswith("_") and sig not in methods:
                methods.append(sig)
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

    for match in _STATE_MODEL_RE.finditer(source_text):
        model_name = match.group(1)
        if model_name not in state_models:
            state_models.append(model_name)

    primary_symbol = (
        classes[0]
        if classes
        else (functions[0] if functions else Path(rel_path).stem)
    )
    symbol_type = _infer_symbol_type(source_text, primary_symbol, rel_path)
    loc = sum(1 for line in source_text.splitlines() if line.strip())

    return FileSymbolCard(
        file_path=rel_path,
        language=language,
        package_name=package_name,
        primary_symbol=primary_symbol,
        symbol_type=symbol_type,
        imports=imports[:30],
        classes=classes[:25],
        functions=functions[:15],
        methods=methods[:8],
        state_models=state_models[:10],
        loc=loc,
    )


_REGEX_PACKAGE = re.compile(r"^\s*package\s+([A-Za-z0-9_.]+)", re.MULTILINE)
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
    r"^\s*(?:export\s+)?(?:suspend\s+)?(?:async\s+)?(?:def|fun|func|function)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)


def _extract_with_regex(source_text: str, language: str, rel_path: str) -> FileSymbolCard:
    """Fallback regex extractor when Tree-sitter parsing is unavailable."""
    pkg_match = _REGEX_PACKAGE.search(source_text)
    package_name = pkg_match.group(1) if pkg_match else ""
    imports = [_clean_import_text(m.group(0)) for m in _REGEX_IMPORT.finditer(source_text)]
    classes = list(dict.fromkeys(m.group(1) for m in _REGEX_CLASS.finditer(source_text)))
    functions = list(dict.fromkeys(m.group(1) for m in _REGEX_FUNC.finditer(source_text)))
    state_models = list(dict.fromkeys(m.group(1) for m in _STATE_MODEL_RE.finditer(source_text)))

    primary_symbol = (
        classes[0]
        if classes
        else (functions[0] if functions else Path(rel_path).stem)
    )
    symbol_type = _infer_symbol_type(source_text, primary_symbol, rel_path)
    loc = sum(1 for line in source_text.splitlines() if line.strip())

    return FileSymbolCard(
        file_path=rel_path,
        language=language,
        package_name=package_name,
        primary_symbol=primary_symbol,
        symbol_type=symbol_type,
        imports=list(dict.fromkeys(imports))[:30],
        classes=classes[:25],
        functions=functions[:15],
        methods=functions[:8],
        state_models=state_models[:10],
        loc=loc,
    )


def extract_symbol_card(
    file_path: str | Path,
    rel_path: str | None = None,
) -> FileSymbolCard | None:
    """Extract a rich FileSymbolCard (symbols, methods, state models, imports, LOC)."""
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


def extract_file_symbols(
    file_path: str | Path,
    rel_path: str | None = None,
) -> FileSymbolCard | None:
    """Extract imports, classes, and top-level functions from a source file.

    Returns a FileSymbolCard (subclass of FileSymbols) for v0.2 compatibility.
    """
    return extract_symbol_card(file_path, rel_path=rel_path)
