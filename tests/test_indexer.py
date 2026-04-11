"""Tests for mclaude code indexer."""
from __future__ import annotations

from pathlib import Path

import pytest

from mclaude.indexer import CodeIndex, parse_module


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """Create a minimal Python project to index."""
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('"""My app."""\n__version__ = "1.0"', encoding="utf-8")

    (pkg / "core.py").write_text('''"""
Core module - business logic.

Handles the main workflow including validation and processing.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Config:
    """Application configuration."""
    name: str
    debug: bool = False

    def validate(self) -> bool:
        """Check if config is valid."""
        return bool(self.name)


class Engine:
    """Main processing engine."""

    def __init__(self, config: Config) -> None:
        self.config = config

    def run(self, data: list[str]) -> dict:
        """Process data and return results."""
        return {"processed": len(data)}

    def _internal(self) -> None:
        """Should not appear in index."""
        pass


def create_engine(name: str) -> Engine:
    """Factory function for Engine."""
    return Engine(Config(name=name))


def _private_helper():
    """Should not appear in index."""
    pass
''', encoding="utf-8")

    (pkg / "utils.py").write_text('''"""Utility functions."""

def slugify(text: str) -> str:
    """Convert text to slug."""
    return text.lower().replace(" ", "-")
''', encoding="utf-8")

    # Test file should be excluded
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text('"""Tests."""\ndef test_something(): pass', encoding="utf-8")

    return tmp_path


class TestParseModule:
    def test_extracts_docstring(self, sample_project: Path):
        info = parse_module(sample_project / "myapp" / "core.py", sample_project)
        assert info is not None
        assert "Core module" in info.docstring

    def test_extracts_classes(self, sample_project: Path):
        info = parse_module(sample_project / "myapp" / "core.py", sample_project)
        assert len(info.classes) == 2
        names = [c.name for c in info.classes]
        assert "Config" in names
        assert "Engine" in names

    def test_excludes_private_methods(self, sample_project: Path):
        info = parse_module(sample_project / "myapp" / "core.py", sample_project)
        engine = next(c for c in info.classes if c.name == "Engine")
        method_names = [m.name for m in engine.methods]
        assert "__init__" in method_names
        assert "run" in method_names
        assert "_internal" not in method_names

    def test_extracts_public_functions(self, sample_project: Path):
        info = parse_module(sample_project / "myapp" / "core.py", sample_project)
        func_names = [f.name for f in info.functions]
        assert "create_engine" in func_names
        assert "_private_helper" not in func_names

    def test_extracts_return_type(self, sample_project: Path):
        info = parse_module(sample_project / "myapp" / "core.py", sample_project)
        create = next(f for f in info.functions if f.name == "create_engine")
        assert create.returns == "Engine"

    def test_line_count(self, sample_project: Path):
        info = parse_module(sample_project / "myapp" / "core.py", sample_project)
        assert info.line_count > 30


class TestCodeIndex:
    def test_scan_finds_modules(self, sample_project: Path):
        idx = CodeIndex(sample_project)
        idx.scan()
        paths = [m.relative_path for m in idx.modules]
        assert "myapp/core.py" in paths
        assert "myapp/utils.py" in paths

    def test_scan_excludes_tests(self, sample_project: Path):
        idx = CodeIndex(sample_project)
        idx.scan()
        paths = [m.relative_path for m in idx.modules]
        assert not any("test_" in p for p in paths)

    def test_stats(self, sample_project: Path):
        idx = CodeIndex(sample_project)
        idx.scan()
        stats = idx.stats()
        assert stats["modules"] >= 2
        assert stats["classes"] >= 2
        assert stats["functions"] >= 2

    def test_write_code_map(self, sample_project: Path):
        idx = CodeIndex(sample_project)
        idx.scan()
        path = idx.write_code_map()
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "# Code Map" in content
        assert "Config" in content
        assert "Engine" in content
        assert "create_engine" in content

    def test_write_llms_txt(self, sample_project: Path):
        idx = CodeIndex(sample_project)
        idx.scan()
        path = idx.write_llms_txt()
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "myapp/core.py" in content
        assert "Core module" in content

    def test_code_map_excludes_private(self, sample_project: Path):
        idx = CodeIndex(sample_project)
        idx.scan()
        content = idx.render_code_map()
        assert "_private_helper" not in content
        assert "_internal" not in content

    def test_auto_scan_on_render(self, sample_project: Path):
        """Should auto-scan if not explicitly called."""
        idx = CodeIndex(sample_project)
        content = idx.render_code_map()
        assert "Config" in content


class TestCodeIndexOnMclaude:
    """Test indexer on the actual mclaude project."""

    def test_index_mclaude(self):
        project_root = Path(__file__).parent.parent
        idx = CodeIndex(project_root)
        idx.scan()
        stats = idx.stats()
        assert stats["modules"] >= 10
        assert stats["classes"] >= 10

        # Should find core modules
        paths = [m.relative_path for m in idx.modules]
        assert any("locks.py" in p for p in paths)
        assert any("messages.py" in p for p in paths)
        assert any("mail.py" in p for p in paths)
