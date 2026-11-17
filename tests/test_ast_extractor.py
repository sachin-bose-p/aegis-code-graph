"""Tests for Tree-sitter AST symbol and import extraction."""

from __future__ import annotations

from pathlib import Path

from aegis_code_graph.ast_extractor import _extract_with_regex, extract_file_symbols


def test_extract_dart_symbols(tmp_path: Path) -> None:
    file_path = tmp_path / "home_screen.dart"
    file_path.write_text(
        """
import 'package:flutter/material.dart';
import '../bloc/auth_bloc.dart';

class HomeScreen extends StatelessWidget {
  void build() {
    // body should not affect extraction
  }
}
""",
        encoding="utf-8",
    )
    sym = extract_file_symbols(file_path, rel_path="lib/home_screen.dart")
    assert sym is not None
    assert sym.language == "dart"
    assert "HomeScreen" in sym.classes
    assert any("flutter/material.dart" in imp for imp in sym.imports)
    assert any("auth_bloc.dart" in imp for imp in sym.imports)


def test_extract_kotlin_symbols(tmp_path: Path) -> None:
    file_path = tmp_path / "MainActivity.kt"
    file_path.write_text(
        """
import androidx.appcompat.app.AppCompatActivity
import com.example.auth.AuthRepo

class MainActivity : AppCompatActivity() {
    fun onCreate() {}
}
""",
        encoding="utf-8",
    )
    sym = extract_file_symbols(file_path)
    assert sym is not None
    assert sym.language == "kotlin"
    assert "MainActivity" in sym.classes
    assert len(sym.imports) == 2


def test_extract_swift_symbols(tmp_path: Path) -> None:
    file_path = tmp_path / "AppDelegate.swift"
    file_path.write_text(
        """
import UIKit
import Foundation

class AppDelegate: UIResponder {
    func application() {}
}
""",
        encoding="utf-8",
    )
    sym = extract_file_symbols(file_path)
    assert sym is not None
    assert sym.language == "swift"
    assert "AppDelegate" in sym.classes
    assert any("UIKit" in imp for imp in sym.imports)


def test_extract_typescript_and_python_symbols(tmp_path: Path) -> None:
    ts_file = tmp_path / "App.tsx"
    ts_file.write_text(
        """
import React from 'react';
import { AuthProvider } from './auth';

export const App = () => {
  return null;
};

export class SessionManager {}
""",
        encoding="utf-8",
    )
    ts_sym = extract_file_symbols(ts_file)
    assert ts_sym is not None
    assert "App" in ts_sym.classes
    assert "SessionManager" in ts_sym.classes

    py_file = tmp_path / "service.py"
    py_file.write_text(
        """
import os
from fastapi import APIRouter

class UserRepository:
    def get_user(self):
        return None

def bootstrap():
    pass
""",
        encoding="utf-8",
    )
    py_sym = extract_file_symbols(py_file)
    assert py_sym is not None
    assert "UserRepository" in py_sym.classes
    assert "bootstrap" in py_sym.functions
    assert len(py_sym.imports) == 2


def test_regex_fallback_extracts_symbols() -> None:
    sym = _extract_with_regex(
        "import os\nclass FallbackService:\n    pass\ndef helper():\n    pass\n",
        language="python",
        rel_path="fallback.py",
    )
    assert "FallbackService" in sym.classes
    assert "helper" in sym.functions
    assert "import os" in sym.imports
