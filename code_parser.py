"""Tree-sitter parsing and language-specific structure extraction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import tree_sitter_c
import tree_sitter_c_sharp
import tree_sitter_cpp
import tree_sitter_go
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_php
import tree_sitter_python
import tree_sitter_ruby
import tree_sitter_rust
import tree_sitter_typescript
from tree_sitter import Language, Parser


GRAMMARS: dict[str, Language] = {
    "Python": Language(tree_sitter_python.language()),
    "JavaScript": Language(tree_sitter_javascript.language()),
    "TypeScript": Language(tree_sitter_typescript.language_typescript()),
    "Java": Language(tree_sitter_java.language()),
    "C": Language(tree_sitter_c.language()),
    "C++": Language(tree_sitter_cpp.language()),
    "C#": Language(tree_sitter_c_sharp.language()),
    "Go": Language(tree_sitter_go.language()),
    "Rust": Language(tree_sitter_rust.language()),
    "PHP": Language(tree_sitter_php.language_php()),
    "Ruby": Language(tree_sitter_ruby.language()),
}

MAX_SNIPPET_CHARS = 32_768
CLASS_NODE_TYPES: dict[str, set[str]] = {
    "Python": {"class_definition"},
    "JavaScript": {"class_declaration", "class_expression"},
    "TypeScript": {"class_declaration", "abstract_class_declaration", "interface_declaration"},
    "Java": {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"},
    "C": {"struct_specifier", "enum_specifier"},
    "C++": {"class_specifier", "struct_specifier", "enum_specifier"},
    "C#": {"class_declaration", "struct_declaration", "interface_declaration", "record_declaration", "enum_declaration"},
    "Go": {"type_spec"},
    "Rust": {"struct_item", "enum_item", "trait_item", "union_item"},
    "PHP": {"class_declaration", "interface_declaration", "trait_declaration", "enum_declaration"},
    "Ruby": {"class", "module"},
}
FUNCTION_NODE_TYPES: dict[str, set[str]] = {
    "Python": {"function_definition"},
    "JavaScript": {"function_declaration", "generator_function_declaration", "function_expression", "arrow_function", "method_definition"},
    "TypeScript": {"function_declaration", "generator_function_declaration", "function_expression", "arrow_function", "method_definition", "method_signature"},
    "Java": {"method_declaration", "constructor_declaration"},
    "C": {"function_definition"},
    "C++": {"function_definition"},
    "C#": {"method_declaration", "constructor_declaration", "local_function_statement"},
    "Go": {"function_declaration", "method_declaration"},
    "Rust": {"function_item"},
    "PHP": {"function_definition", "method_declaration"},
    "Ruby": {"method", "singleton_method"},
}


@dataclass(frozen=True)
class ParsedSource:
    symbols: list[dict[str, Any]]
    imports: list[dict[str, Any]]
    has_syntax_errors: bool
    syntax_error_count: int


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _named_child(node: Any, field: str) -> Any | None:
    return node.child_by_field_name(field)


def _first_named(node: Any, node_types: set[str]) -> Any | None:
    return next((child for child in node.named_children if child.type in node_types), None)


def _deep_named(node: Any, node_types: set[str]) -> Any | None:
    if node.type in node_types:
        return node
    for child in node.named_children:
        found = _deep_named(child, node_types)
        if found is not None:
            return found
    return None


def _line_starts(source: bytes) -> list[int]:
    return [0, *(index + 1 for index, byte in enumerate(source) if byte == 10)]


def _source_column(source: bytes, line_starts: list[int], row: int, byte_column: int) -> int:
    if row >= len(line_starts):
        return 1
    start = line_starts[row]
    return len(source[start : start + byte_column].decode("utf-8", errors="replace")) + 1


def _end_line(row: int, column: int, start_line: int) -> int:
    # Tree-sitter end points are exclusive. A zero column means the source
    # ended at the start of that row, so the previous row was the last line.
    return max(start_line, row + (1 if column > 0 else 0))


def _symbol_kind(language: str, node: Any, parent_kind: str | None) -> str | None:
    node_type = node.type
    if language in {"C", "C++"} and node_type in {"declaration", "field_declaration"}:
        declarator = _deep_named(node, {"function_declarator"})
        if declarator is not None:
            return "method" if parent_kind == "class" else "function"
    if node_type in CLASS_NODE_TYPES.get(language, set()):
        if language == "Go" and not any(child.type in {"struct_type", "interface_type"} for child in node.named_children):
            return None
        return "class"

    if node_type not in FUNCTION_NODE_TYPES.get(language, set()):
        return None
    if language in {"Java", "C#"}:
        return "method"
    if language == "Go":
        return "method" if node_type == "method_declaration" else "function"
    if language == "TypeScript" and node_type == "method_signature":
        return "method"
    if language in {"JavaScript", "TypeScript"} and node_type == "method_definition":
        return "method"
    if language in {"Ruby", "PHP"} and node_type in {"method", "singleton_method", "method_declaration"}:
        return "method"
    if language in {"C", "C++"} and parent_kind == "class":
        return "method"
    if language == "Rust" and parent_kind in {"class", "impl"}:
        return "method"
    if language == "Python" and parent_kind == "class":
        return "method"
    return "function"


def _symbol_name(language: str, node: Any, source: bytes) -> str | None:
    if language in {"JavaScript", "TypeScript"} and node.type == "arrow_function":
        parent = node.parent
        if parent is not None and parent.type == "variable_declarator":
            target = _named_child(parent, "name") or _first_named(parent, {"identifier"})
            if target is not None:
                return _text(target, source).strip()
    name_node = _named_child(node, "name")
    if name_node is not None:
        return _text(name_node, source).strip()
    if language in {"C", "C++"} and node.type in {"function_definition", "declaration", "field_declaration"}:
        declarator = _deep_named(node, {"function_declarator"})
        if declarator is not None:
            target = _named_child(declarator, "declarator")
            target = target or _first_named(declarator, {"identifier", "field_identifier", "operator_name"})
            if target is not None:
                return _text(target, source).strip()
    if language == "Go" and node.type == "type_spec":
        candidate = _first_named(node, {"type_identifier"})
        return _text(candidate, source).strip() if candidate is not None else None
    if language in {"Python", "JavaScript", "TypeScript", "Java", "C", "C++", "C#", "Go", "Rust", "Ruby"}:
        target = _first_named(node, {"identifier", "type_identifier", "field_identifier", "property_identifier", "constant"})
        if target is not None:
            return _text(target, source).strip()
    if language == "PHP":
        target = _first_named(node, {"name"})
        if target is not None:
            return _text(target, source).strip()
    return None


def _go_receiver_name(node: Any, source: bytes) -> str | None:
    if node.type != "method_declaration":
        return None
    receiver = next((child for child in node.named_children if child.type == "parameter_list"), None)
    if receiver is None:
        return None
    parameter = next((child for child in receiver.named_children if child.type == "parameter_declaration"), None)
    if parameter is None:
        return None
    type_node = next(
        (child for child in parameter.named_children if child.type in {"type_identifier", "pointer_type"}),
        None,
    )
    if type_node is not None and type_node.type == "pointer_type":
        type_node = next((child for child in type_node.named_children if child.type == "type_identifier"), None)
    return _text(type_node, source).lstrip("*") if type_node is not None else None


def _rust_impl_name(node: Any, source: bytes) -> str | None:
    parent = node.parent
    while parent is not None and parent.type != "impl_item":
        parent = parent.parent
    if parent is None:
        return None
    target = next(
        (child for child in parent.named_children if child.type in {"type_identifier", "generic_type"}),
        None,
    )
    return _text(target, source).split("<", 1)[0].strip() if target is not None else None


_PARAMETER_NODES = {
    "parameters", "formal_parameters", "parameter_list", "parameter_declaration_list",
    "method_parameters", "parameter_list", "type_parameters",
}


def _parameters(node: Any, source: bytes) -> list[str]:
    parameter_nodes = [child for child in node.named_children if child.type in _PARAMETER_NODES]
    if not parameter_nodes:
        parameter_node = _deep_named(node, _PARAMETER_NODES)
    elif node.type == "method_declaration" and len(parameter_nodes) > 1:
        parameter_node = parameter_nodes[-1]
    else:
        parameter_node = parameter_nodes[0]
    if parameter_node is None:
        return []
    return [_text(child, source).strip() for child in parameter_node.named_children]


def _stable_symbol_id(repository_id: str, file_path: str, qualified_name: str, start: int, end: int) -> str:
    identity = json.dumps([repository_id, file_path, qualified_name, start, end], ensure_ascii=False)
    return "sym_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _make_symbol(
    repository_id: str,
    file_path: str,
    language: str,
    node: Any,
    source: bytes,
    line_starts: list[int],
    kind: str,
    name: str,
    parent: dict[str, Any] | None,
) -> dict[str, Any]:
    qualified_name = f"{parent['qualified_name']}.{name}" if parent else name
    start_line = node.start_point.row + 1
    end_line = _end_line(node.end_point.row, node.end_point.column, start_line)
    snippet_bytes = source[node.start_byte : node.end_byte]
    snippet = snippet_bytes.decode("utf-8", errors="replace")
    truncated = len(snippet) > MAX_SNIPPET_CHARS
    if truncated:
        snippet = snippet[:MAX_SNIPPET_CHARS]
    return {
        "symbol_id": _stable_symbol_id(repository_id, file_path, f"{kind}:{qualified_name}", node.start_byte, node.end_byte),
        "repository_id": repository_id,
        "file_path": file_path,
        "symbol_name": name,
        "qualified_name": qualified_name,
        "symbol_type": kind,
        "language": language,
        "parent_symbol_id": parent["symbol_id"] if parent else None,
        "parent_symbol": parent["symbol_name"] if parent else None,
        "start_line": start_line,
        "end_line": end_line,
        "start_column": _source_column(source, line_starts, node.start_point.row, node.start_point.column),
        "end_column": _source_column(source, line_starts, node.end_point.row, node.end_point.column),
        "parameters": _parameters(node, source),
        "snippet": snippet,
        "snippet_truncated": truncated,
        "_start_byte": node.start_byte,
        "_end_byte": node.end_byte,
    }


def _import_records(language: str, root: Any, source: bytes, line_starts: list[int]) -> list[dict[str, Any]]:
    imports: list[dict[str, Any]] = []

    def add(node: Any, module: str, imported_symbol: str | None, import_type: str, alias: str | None = None) -> None:
        module = module.strip().strip("\"'")
        if not module:
            return
        imports.append({
            "file_path": "",
            "module": module,
            "imported_symbol": imported_symbol,
            "alias": alias,
            "import_type": import_type,
            "start_line": node.start_point.row + 1,
            "end_line": _end_line(node.end_point.row, node.end_point.column, node.start_point.row + 1),
            "start_column": _source_column(source, line_starts, node.start_point.row, node.start_point.column),
            "end_column": _source_column(source, line_starts, node.end_point.row, node.end_point.column),
            "snippet": _text(node, source),
        })

    pending = [root]
    while pending:
        node = pending.pop()
        node_type = node.type
        if language == "Python" and node_type in {"import_statement", "import_from_statement"}:
            if node_type == "import_from_statement":
                named = list(node.named_children)
                module = _text(named[0], source) if named else ""
                targets = named[1:]
                if not targets:
                    add(node, module, None, "from_import")
                for target in targets:
                    value = _text(target, source)
                    pieces = value.split(" as ", 1)
                    add(node, module, pieces[0], "from_import", pieces[1] if len(pieces) > 1 else None)
            else:
                targets = node.named_children
                for target in targets:
                    value = _text(target, source)
                    pieces = value.split(" as ", 1)
                    add(node, pieces[0], None, "import", pieces[1] if len(pieces) > 1 else None)
        elif language in {"JavaScript", "TypeScript"} and node_type == "import_statement":
            module_node = next((child for child in node.named_children if child.type == "string"), None)
            if module_node is not None:
                module = _text(module_node, source).strip("\"'")
                clause = next((child for child in node.named_children if child.type == "import_clause"), None)
                specs = _deep_named(clause, {"import_specifier"}) if clause is not None else None
                if specs is None:
                    default_import = next(
                        (child for child in clause.named_children if child.type in {"identifier", "namespace_import"}),
                        None,
                    ) if clause is not None else None
                    add(node, module, _text(default_import, source) if default_import is not None else None, "import")
                else:
                    specifiers: list[Any] = []
                    def collect(current: Any) -> None:
                        if current.type == "import_specifier":
                            specifiers.append(current)
                        else:
                            for sub in current.named_children:
                                collect(sub)
                    collect(clause)
                    for specifier in specifiers:
                        add(node, module, _text(specifier, source), "import")
        elif language == "Java" and node_type == "import_declaration":
            value = _text(node, source).removeprefix("import").strip().removesuffix(";").strip()
            is_static = value.startswith("static ")
            if is_static:
                value = value.removeprefix("static ").strip()
            parts = value.rsplit(".", 1)
            add(node, parts[0] if len(parts) > 1 else value, parts[-1] if len(parts) > 1 else None, "static_import" if is_static else "import")
        elif language in {"C", "C++"} and node_type == "preproc_include":
            target = node.named_children[-1] if node.named_children else node
            add(node, _text(target, source), None, "include")
        elif language == "C#" and node_type == "using_directive":
            value = _text(node, source).removeprefix("using").strip().removesuffix(";").strip()
            add(node, value, None, "using")
        elif language == "Go" and node_type == "import_spec":
            value = _text(node, source).strip()
            children = node.named_children
            module_node = next((child for child in children if child.type in {"interpreted_string_literal", "raw_string_literal"}), None)
            if module_node is not None:
                alias = _text(children[0], source) if children and children[0] != module_node else None
                add(node, _text(module_node, source), None, "import", alias)
        elif language == "Rust" and node_type == "use_declaration":
            target = next((child for child in node.named_children if child.type != "visibility_modifier"), None)
            if target is not None:
                add(node, _text(target, source), None, "use")
        elif language == "PHP" and node_type == "namespace_use_clause":
            add(node, _text(node, source), None, "namespace_use")
        elif language == "Ruby" and node_type == "call":
            callee = _named_child(node, "method")
            if callee is None:
                callee = next((child for child in node.named_children if child.type == "identifier"), None)
            if callee is not None and _text(callee, source) in {"require", "require_relative", "autoload"}:
                string_node = _deep_named(node, {"string"})
                if string_node is not None:
                    add(node, _text(string_node, source), None, _text(callee, source))
        pending.extend(reversed(node.named_children))
    return imports


def parse_source(
    source: bytes,
    language: str,
    repository_id: str,
    file_path: str,
) -> ParsedSource:
    """Parse one UTF-8 source file and extract symbols and imports."""
    grammar = GRAMMARS.get(language)
    if language == "TypeScript" and file_path.casefold().endswith(".tsx"):
        grammar = Language(tree_sitter_typescript.language_tsx())
    if grammar is None:
        raise ValueError(f"No Tree-sitter grammar is configured for {language}")
    parser = Parser(grammar)
    tree = parser.parse(source)
    if tree is None:
        raise ValueError("Tree-sitter did not return a syntax tree")
    root = tree.root_node
    line_starts = _line_starts(source)
    symbols: list[dict[str, Any]] = []

    pending = [(root, None, None)]
    while pending:
        node, parent_symbol, parent_kind = pending.pop()
        kind = _symbol_kind(language, node, parent_kind)
        current_parent = parent_symbol
        next_parent_kind = parent_kind
        if kind is not None:
            name = _symbol_name(language, node, source)
            if name:
                symbol = _make_symbol(
                    repository_id, file_path, language, node, source, line_starts, kind, name, parent_symbol
                )
                symbols.append(symbol)
                current_parent = symbol
                next_parent_kind = kind
            elif kind == "class":
                next_parent_kind = "class"
        elif language == "Rust" and node.type == "impl_item":
            next_parent_kind = "impl"
        pending.extend((child, current_parent, next_parent_kind) for child in reversed(node.named_children))

    if language in {"Go", "Rust"}:
        classes_by_name = {item["symbol_name"]: item for item in symbols if item["symbol_type"] == "class"}
        node_by_range: dict[tuple[int, int], Any] = {}
        pending_nodes = [root]
        while pending_nodes:
            current = pending_nodes.pop()
            node_by_range[(current.start_byte, current.end_byte)] = current
            pending_nodes.extend(current.named_children)
        for item in symbols:
            if item["symbol_type"] != "method" or item.get("parent_symbol_id"):
                continue
            method_node = node_by_range.get((item["_start_byte"], item["_end_byte"]))
            if method_node is None:
                receiver_name = None
            elif language == "Go":
                receiver_name = _go_receiver_name(method_node, source)
            else:
                receiver_name = _rust_impl_name(method_node, source)
            parent = classes_by_name.get(receiver_name or "")
            if parent is not None:
                item["parent_symbol_id"] = parent["symbol_id"]
                item["parent_symbol"] = parent["symbol_name"]
                item["qualified_name"] = f"{parent['qualified_name']}.{item['symbol_name']}"
                item["symbol_id"] = _stable_symbol_id(
                    repository_id, file_path, f"{item['symbol_type']}:{item['qualified_name']}",
                    item["_start_byte"], item["_end_byte"],
                )

    imports = _import_records(language, root, source, line_starts)
    for item in imports:
        item["file_path"] = file_path
    syntax_error_count = 0

    if root.has_error:
        pending_nodes = [root]
        while pending_nodes:
            node = pending_nodes.pop()
            if node.type == "ERROR" or node.is_missing:
                syntax_error_count += 1
            pending_nodes.extend(node.children)
        syntax_error_count = max(1, syntax_error_count)
    return ParsedSource(symbols, imports, root.has_error, syntax_error_count)
