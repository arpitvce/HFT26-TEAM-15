from __future__ import annotations

import pytest

from code_parser import GRAMMARS, parse_source


def test_python_extracts_nested_symbols_imports_parameters_and_exact_lines():
    source = (
        "import os\n"
        "from helpers import load as read\n"
        "class Box:\n"
        "    def method(self, value: int):\n"
        "        def nested():\n"
        "            return value\n"
        "        return nested()\n"
        "def top(item):\n"
        "    return item\n"
    ).encode()

    parsed = parse_source(source, "Python", "repo_test", "src/box.py")

    symbols = {item["qualified_name"]: item for item in parsed.symbols}
    assert set(symbols) == {"Box", "Box.method", "Box.method.nested", "top"}
    assert symbols["Box"]["symbol_type"] == "class"
    assert (symbols["Box"]["start_line"], symbols["Box"]["end_line"]) == (3, 7)
    assert symbols["Box.method"]["symbol_type"] == "method"
    assert (symbols["Box.method"]["start_line"], symbols["Box.method"]["end_line"]) == (4, 7)
    assert symbols["Box.method"]["parameters"] == ["self", "value: int"]
    assert symbols["Box.method.nested"]["parent_symbol_id"] == symbols["Box.method"]["symbol_id"]
    assert symbols["Box.method.nested"]["snippet"].startswith("def nested")
    assert symbols["top"]["start_line"] == 8
    assert [(item["module"], item["imported_symbol"], item["alias"]) for item in parsed.imports] == [
        ("os", None, None), ("helpers", "load", "read")
    ]
    assert parsed.has_syntax_errors is False


def test_javascript_and_typescript_declarations_and_imports():
    javascript = parse_source(
        b'import { thing } from "pkg";\nfunction run(value) { return value; }\nconst arrow = (x) => x;\n',
        "JavaScript", "repo_test", "src/app.js",
    )
    assert {item["symbol_name"] for item in javascript.symbols} == {"run", "arrow"}
    assert javascript.imports[0]["module"] == "pkg"
    assert javascript.imports[0]["imported_symbol"] == "thing"

    typescript = parse_source(
        b'import { Widget } from "widgets";\nexport class Panel { render(value: string) {} }\n'
        b'export function build(size: number) { return size; }\n',
        "TypeScript", "repo_test", "src/panel.ts",
    )
    symbols = {item["qualified_name"]: item for item in typescript.symbols}
    assert {"Panel", "Panel.render", "build"} == set(symbols)
    assert symbols["build"]["parameters"] == ["size: number"]
    assert typescript.imports[0]["module"] == "widgets"


@pytest.mark.parametrize(
    ("language", "path", "source"),
    [
        ("Python", "f.py", b"def f(x): return x"),
        ("JavaScript", "f.js", b"function f(x) { return x; }"),
        ("TypeScript", "f.ts", b"function f(x: number) { return x; }"),
        ("Java", "F.java", b"class F { void f(int x) {} }"),
        ("C", "f.c", b"struct Box { int x; }; int declared(int); int f(int x) { return x; }"),
        ("C++", "f.cpp", b"class F { void declared(); void m() {} }; int f() { return 1; }"),
        ("C#", "F.cs", b"class F { void M(int x) {} }"),
        ("Go", "f.go", b"package p\ntype F struct {}\nfunc (f F) M(x int) {}\nfunc Run() {}"),
        ("Rust", "f.rs", b"struct F; impl F { fn m(&self) {} } fn run() {}"),
        ("PHP", "f.php", b"<?php class F { function m() {} } function run() {}"),
        ("Ruby", "f.rb", b"class F\n def m; end\nend\ndef run; end"),
    ],
)
def test_configured_grammar_extracts_symbols(language: str, path: str, source: bytes):
    parsed = parse_source(source, language, "repo_test", path)
    assert language in GRAMMARS
    assert parsed.symbols
    assert parsed.has_syntax_errors is False
    if language in {"C", "C++"}:
        assert any(item["symbol_type"] == "class" for item in parsed.symbols)
        assert any(item["symbol_name"] == "declared" for item in parsed.symbols)


def test_empty_and_malformed_files_are_handled_without_crashing():
    empty = parse_source(b"", "Python", "repo_test", "empty.py")
    assert empty.symbols == []
    assert empty.imports == []
    assert empty.has_syntax_errors is False

    malformed = parse_source(b"def broken(:\n    pass\n", "Python", "repo_test", "broken.py")
    assert malformed.has_syntax_errors is True
    assert malformed.syntax_error_count >= 1


def test_tsx_uses_tsx_grammar_and_unicode_columns_are_character_based():
    parsed = parse_source("const π = () => 1;\n".encode(), "TypeScript", "repo_test", "ui.tsx")
    assert parsed.symbols[0]["symbol_name"] == "π"
    assert parsed.symbols[0]["start_column"] == 11


def test_go_and_rust_methods_link_to_declared_type_symbols():
    go = parse_source(
        b"package p\ntype Store struct {}\nfunc (s Store) Save() {}\n",
        "Go", "repo_test", "store.go",
    )
    rust = parse_source(
        b"struct Store;\nimpl Store { fn save(&self) {} }\n",
        "Rust", "repo_test", "store.rs",
    )
    for parsed in (go, rust):
        type_symbol = next(item for item in parsed.symbols if item["symbol_type"] == "class")
        method = next(item for item in parsed.symbols if item["symbol_type"] == "method")
        assert method["parent_symbol_id"] == type_symbol["symbol_id"]
        assert method["qualified_name"].startswith("Store.")


@pytest.mark.parametrize(
    ("language", "path", "source"),
    [
        ("Python", "f.py", b"import os"),
        ("JavaScript", "f.js", b'import x from "x";'),
        ("TypeScript", "f.ts", b'import x from "x";'),
        ("Java", "F.java", b"import java.util.List; class F {}"),
        ("C", "f.c", b"#include <stdio.h>\n"),
        ("C++", "f.cpp", b"#include <vector>\n"),
        ("C#", "F.cs", b"using System; class F {}"),
        ("Go", "f.go", b'package p\nimport "fmt"'),
        ("Rust", "f.rs", b"use std::fmt;"),
        ("PHP", "f.php", b"<?php use A\\B;"),
        ("Ruby", "f.rb", b'require "json"'),
    ],
)
def test_import_extraction_across_configured_languages(language: str, path: str, source: bytes):
    parsed = parse_source(source, language, "repo_test", path)
    assert parsed.imports
    assert parsed.imports[0]["module"]


def test_unsupported_language_has_explicit_error():
    with pytest.raises(ValueError, match="No Tree-sitter grammar"):
        parse_source(b"text", "HTML", "repo_test", "index.html")
