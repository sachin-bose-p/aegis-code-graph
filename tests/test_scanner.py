"""Tests for repository scanning and token-budgeted context building."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis_code_graph.models import GraphConfig
from aegis_code_graph.scanner import scan_repository, walk_repository


def _create_sample_flutter_repo(root: Path) -> None:
    (root / ".gitignore").write_text("*.secret\ncustom_ignored/\n", encoding="utf-8")
    (root / "README.md").write_text("# Sample Flutter App\n", encoding="utf-8")
    (root / "pubspec.yaml").write_text("name: sample_app\n", encoding="utf-8")
    (root / "pubspec.lock").write_text("# lockfile\n", encoding="utf-8")

    (root / "lib").mkdir()
    (root / "lib" / "main.dart").write_text(
        "import 'package:flutter/material.dart';\nvoid main() {}\n", encoding="utf-8"
    )
    (root / "lib" / "auth_service.dart").write_text(
        "class AuthService {\n  void login() {}\n}\n", encoding="utf-8"
    )

    # Ignored directories and files
    (root / ".dart_tool").mkdir()
    (root / ".dart_tool" / "package_config.json").write_text("{}", encoding="utf-8")
    (root / "build").mkdir()
    (root / "build" / "app.apk").write_bytes(b"binary")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("", encoding="utf-8")
    (root / "custom_ignored").mkdir()
    (root / "custom_ignored" / "temp.dart").write_text("", encoding="utf-8")
    (root / "api.secret").write_text("top-secret", encoding="utf-8")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n")


def test_walk_repository_filters_noise_and_gitignore(tmp_path: Path) -> None:
    _create_sample_flutter_repo(tmp_path)
    files = walk_repository(tmp_path)

    assert "README.md" in files
    assert "pubspec.yaml" in files
    assert "lib/main.dart" in files
    assert "lib/auth_service.dart" in files

    # Ignored items must be absent
    assert "pubspec.lock" not in files
    assert ".dart_tool/package_config.json" not in files
    assert "build/app.apk" not in files
    assert "node_modules/pkg/index.js" not in files
    assert "custom_ignored/temp.dart" not in files
    assert "api.secret" not in files
    assert "logo.png" not in files


def test_scan_repository_extracts_manifests_entrypoints_and_ast(tmp_path: Path) -> None:
    _create_sample_flutter_repo(tmp_path)
    ctx = scan_repository(tmp_path, GraphConfig(max_tokens_budget=8000))

    assert ctx.repo_name == tmp_path.name
    assert "pubspec.yaml" in ctx.manifests
    assert "README.md" in ctx.manifests
    assert "lib/main.dart" in ctx.entrypoints
    assert any(
        sym.file_path == "lib/auth_service.dart" and "AuthService" in sym.classes
        for sym in ctx.ast_map
    )
    assert ctx.estimated_tokens <= 8000


def test_scan_repository_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_repository(tmp_path / "does_not_exist")


def test_walk_repository_skips_apk_decompile_boilerplate(tmp_path: Path) -> None:
    (tmp_path / "jadx" / "sources" / "com" / "app").mkdir(parents=True)
    (tmp_path / "jadx" / "sources" / "com" / "app" / "MainActivity.java").write_text(
        "class MainActivity {}", encoding="utf-8"
    )
    (tmp_path / "jadx" / "sources" / "androidx" / "core").mkdir(parents=True)
    (tmp_path / "jadx" / "sources" / "androidx" / "core" / "View.java").write_text(
        "class View {}", encoding="utf-8"
    )
    (tmp_path / "apktool" / "smali_classes2" / "com").mkdir(parents=True)
    (tmp_path / "apktool" / "smali_classes2" / "com" / "Foo.smali").write_text("", encoding="utf-8")
    (tmp_path / "apktool" / "res" / "drawable").mkdir(parents=True)
    (tmp_path / "apktool" / "res" / "drawable" / "icon.xml").write_text("", encoding="utf-8")
    (tmp_path / "apktool" / "res" / "layout").mkdir(parents=True)
    (tmp_path / "apktool" / "res" / "layout" / "activity_main.xml").write_text("", encoding="utf-8")

    files = walk_repository(tmp_path)
    assert "jadx/sources/com/app/MainActivity.java" in files
    assert "apktool/res/layout/activity_main.xml" in files
    assert "jadx/sources/androidx/core/View.java" not in files
    assert "apktool/smali_classes2/com/Foo.smali" not in files
    assert "apktool/res/drawable/icon.xml" not in files
