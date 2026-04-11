"""
Code indexer - generates machine-readable project maps from Python source.

Scans a codebase and produces two outputs:
1. `code-map.md` - human/agent-readable architecture overview
2. `llms.txt` - machine-readable index (llms.txt standard)

The index is generated from AST parsing (no execution, no imports needed).
Each module's docstring, classes, functions, and their signatures are extracted.

Usage:

    from mclaude.indexer import CodeIndex

    idx = CodeIndex("/path/to/project")
    idx.scan()                          # scan all .py files
    idx.write_code_map()                # -> .claude/code-map.md
    idx.write_llms_txt()                # -> .claude/llms.txt

    # Or from CLI:
    mclaude index                       # scan cwd, write both files
    mclaude index --path /other/project
    mclaude index --format code-map     # only code-map.md
    mclaude index --format llms-txt     # only llms.txt

The code-map is designed to be read by an agent at session start (via
SessionStart hook or CLAUDE.md reference). One file, full project overview,
no need to read 30 source files to understand the architecture.

Design: the index is a SNAPSHOT, not a live view. Regenerate after significant
changes. Stale index is better than no index - the agent can always fall back
to reading source files directly.
"""
from __future__ import annotations

import ast
import os
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FunctionInfo:
    """Extracted info about a function or method."""
    name: str
    args: list[str]
    returns: str | None
    docstring: str
    is_method: bool = False
    is_classmethod: bool = False
    is_staticmethod: bool = False
    is_property: bool = False
    line: int = 0


@dataclass
class ClassInfo:
    """Extracted info about a class."""
    name: str
    bases: list[str]
    docstring: str
    methods: list[FunctionInfo] = field(default_factory=list)
    line: int = 0


@dataclass
class ModuleInfo:
    """Extracted info about a Python module."""
    path: Path
    relative_path: str
    docstring: str
    classes: list[ClassInfo] = field(default_factory=list)
    functions: list[FunctionInfo] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    line_count: int = 0


def _extract_arg_name(arg: ast.arg) -> str:
    """Get argument name with optional annotation."""
    if arg.annotation:
        try:
            ann = ast.unparse(arg.annotation)
            return f"{arg.arg}: {ann}"
        except Exception:
            pass
    return arg.arg


def _extract_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> FunctionInfo:
    """Extract function/method info from AST node."""
    args = []
    for arg in node.args.args:
        if arg.arg == "self" or arg.arg == "cls":
            continue
        args.append(_extract_arg_name(arg))

    returns = None
    if node.returns:
        try:
            returns = ast.unparse(node.returns)
        except Exception:
            pass

    # Check decorators
    is_classmethod = False
    is_staticmethod = False
    is_property = False
    for dec in node.decorator_list:
        name = ""
        if isinstance(dec, ast.Name):
            name = dec.id
        elif isinstance(dec, ast.Attribute):
            name = dec.attr
        if name == "classmethod":
            is_classmethod = True
        elif name == "staticmethod":
            is_staticmethod = True
        elif name == "property":
            is_property = True

    return FunctionInfo(
        name=node.name,
        args=args,
        returns=returns,
        docstring=ast.get_docstring(node) or "",
        is_classmethod=is_classmethod,
        is_staticmethod=is_staticmethod,
        is_property=is_property,
        line=node.lineno,
    )


def _extract_class(node: ast.ClassDef) -> ClassInfo:
    """Extract class info from AST node."""
    bases = []
    for base in node.bases:
        try:
            bases.append(ast.unparse(base))
        except Exception:
            bases.append("?")

    methods = []
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child.name.startswith("_") and child.name != "__init__":
                continue
            fi = _extract_function(child)
            fi.is_method = True
            methods.append(fi)

    return ClassInfo(
        name=node.name,
        bases=bases,
        docstring=ast.get_docstring(node) or "",
        methods=methods,
        line=node.lineno,
    )


def parse_module(path: Path, project_root: Path) -> ModuleInfo | None:
    """Parse a single Python file and extract its structure."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    relative = str(path.relative_to(project_root)).replace("\\", "/")

    classes = []
    functions = []
    imports = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            classes.append(_extract_class(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                functions.append(_extract_function(node))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)

    return ModuleInfo(
        path=path,
        relative_path=relative,
        docstring=ast.get_docstring(tree) or "",
        classes=classes,
        functions=functions,
        imports=imports,
        line_count=len(source.splitlines()),
    )


class CodeIndex:
    """Scans a Python project and generates machine-readable indexes."""

    def __init__(self, project_root: str | Path | None = None) -> None:
        self.root = Path(project_root) if project_root else Path.cwd()
        self.modules: list[ModuleInfo] = []
        self._scanned = False

    def scan(
        self,
        patterns: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> None:
        """Scan Python files in the project.

        Args:
            patterns: glob patterns to scan (default: all .py files)
            exclude: directory names to skip (default: common non-source dirs)
        """
        if patterns is None:
            patterns = ["**/*.py"]
        if exclude is None:
            exclude = [
                "__pycache__", ".git", ".venv", "venv", "node_modules",
                ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
                "site", "site-packages",
            ]

        exclude_set = set(exclude)
        self.modules = []

        for pattern in patterns:
            for path in sorted(self.root.glob(pattern)):
                # Skip excluded directories
                if any(part in exclude_set for part in path.parts):
                    continue
                # Skip test files by default (they're not API)
                if "test" in path.name.lower() and path.name != "__init__.py":
                    continue
                # Skip __init__.py with no meaningful content
                if path.name == "__init__.py":
                    content = path.read_text(encoding="utf-8").strip()
                    if len(content) < 50:
                        continue

                info = parse_module(path, self.root)
                if info and (info.classes or info.functions or info.docstring):
                    self.modules.append(info)

        self._scanned = True

    def _ensure_scanned(self) -> None:
        if not self._scanned:
            self.scan()

    # -- Code Map output ----------------------------------------------------

    def render_code_map(self) -> str:
        """Render the full code-map as markdown."""
        self._ensure_scanned()

        lines = [
            f"# Code Map - {self.root.name}",
            "",
            f"Generated: {time.strftime('%Y-%m-%d %H:%M')}",
            f"Modules: {len(self.modules)}",
            f"Total lines: {sum(m.line_count for m in self.modules)}",
            "",
            "---",
            "",
        ]

        # Table of contents
        lines.append("## Modules")
        lines.append("")
        for mod in self.modules:
            summary = mod.docstring.split("\n")[0][:80] if mod.docstring else ""
            lines.append(f"- **{mod.relative_path}** - {summary}")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Detailed sections
        for mod in self.modules:
            lines.append(f"## {mod.relative_path}")
            lines.append("")
            if mod.docstring:
                # First paragraph only
                first_para = mod.docstring.split("\n\n")[0].strip()
                lines.append(first_para)
                lines.append("")

            lines.append(f"Lines: {mod.line_count}")
            lines.append("")

            # Classes
            for cls in mod.classes:
                bases_str = f"({', '.join(cls.bases)})" if cls.bases else ""
                lines.append(f"### class {cls.name}{bases_str}")
                lines.append("")
                if cls.docstring:
                    first_line = cls.docstring.split("\n")[0]
                    lines.append(f"{first_line}")
                    lines.append("")

                if cls.methods:
                    for method in cls.methods:
                        if method.name == "__init__":
                            args_str = ", ".join(method.args)
                            lines.append(f"- `__init__({args_str})`")
                        elif method.is_property:
                            ret = f" -> {method.returns}" if method.returns else ""
                            lines.append(f"- `@property {method.name}{ret}`")
                        else:
                            args_str = ", ".join(method.args)
                            ret = f" -> {method.returns}" if method.returns else ""
                            prefix = "@classmethod " if method.is_classmethod else ""
                            lines.append(f"- `{prefix}{method.name}({args_str}){ret}`")
                            if method.docstring:
                                first_line = method.docstring.split("\n")[0]
                                lines.append(f"  {first_line}")
                    lines.append("")

            # Module-level functions
            public_funcs = [f for f in mod.functions if not f.name.startswith("cmd_")]
            if public_funcs:
                lines.append("### Functions")
                lines.append("")
                for func in public_funcs:
                    args_str = ", ".join(func.args)
                    ret = f" -> {func.returns}" if func.returns else ""
                    lines.append(f"- `{func.name}({args_str}){ret}`")
                    if func.docstring:
                        first_line = func.docstring.split("\n")[0]
                        lines.append(f"  {first_line}")
                lines.append("")

            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def write_code_map(self, output: Path | None = None) -> Path:
        """Write code-map.md to .claude/ directory."""
        self._ensure_scanned()
        path = output or (self.root / ".claude" / "code-map.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render_code_map(), encoding="utf-8")
        return path

    # -- llms.txt output ----------------------------------------------------

    def render_llms_txt(self) -> str:
        """Render llms.txt (machine-readable project index)."""
        self._ensure_scanned()

        lines = [
            f"# {self.root.name}",
            "",
            f"> Multi-session collaboration for Claude Code.",
            f"> {len(self.modules)} modules, {sum(m.line_count for m in self.modules)} lines.",
            "",
        ]

        # Group by package
        packages: dict[str, list[ModuleInfo]] = {}
        for mod in self.modules:
            parts = mod.relative_path.split("/")
            pkg = parts[0] if len(parts) > 1 else "(root)"
            packages.setdefault(pkg, []).append(mod)

        for pkg, mods in sorted(packages.items()):
            lines.append(f"## {pkg}")
            for mod in mods:
                summary = mod.docstring.split("\n")[0] if mod.docstring else ""
                lines.append(f"- [{mod.relative_path}](/{mod.relative_path}): {summary}")
            lines.append("")

        return "\n".join(lines)

    def write_llms_txt(self, output: Path | None = None) -> Path:
        """Write llms.txt to project root."""
        self._ensure_scanned()
        path = output or (self.root / "llms.txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render_llms_txt(), encoding="utf-8")
        return path

    # -- Stats --------------------------------------------------------------

    def stats(self) -> dict:
        """Summary statistics about the indexed codebase."""
        self._ensure_scanned()
        total_classes = sum(len(m.classes) for m in self.modules)
        total_funcs = sum(len(m.functions) for m in self.modules)
        total_methods = sum(
            len(c.methods) for m in self.modules for c in m.classes
        )
        return {
            "modules": len(self.modules),
            "classes": total_classes,
            "functions": total_funcs,
            "methods": total_methods,
            "total_lines": sum(m.line_count for m in self.modules),
        }
