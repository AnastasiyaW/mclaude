#!/usr/bin/env python3
"""
Project Knowledge Base scaffolding tool.

Creates a MkDocs-based knowledge base tailored to a specific project.
Each domain becomes a nav section with an index page.

Usage:
    python scaffold.py --name "my-project" --domains "backend,frontend,infra"
    python scaffold.py --name "my-project" --domains "backend,frontend" --port 8200
    python scaffold.py --name "my-project" --domains "backend,frontend" --output ./my-kb
"""

import argparse
import os
import sys
import textwrap
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "template"


def slugify(text: str) -> str:
    return text.strip().lower().replace(" ", "-").replace("_", "-")


def title_case(slug: str) -> str:
    return slug.replace("-", " ").title()


def generate_mkdocs_yml(name: str, domains: list[str], port: int) -> str:
    lines = [
        f'site_name: "{title_case(name)} Knowledge Base"',
        "site_description: >-",
        f"  Internal knowledge base for the {title_case(name)} project.",
        "  Modules, decisions, API references, and gotchas.",
        f'site_url: "http://localhost:{port}/"',
        "",
        "theme:",
        "  name: material",
        "  language: en",
        "  palette:",
        "    - scheme: slate",
        "      primary: deep purple",
        "      accent: amber",
        "      toggle:",
        "        icon: material/brightness-4",
        "        name: Switch to light mode",
        "    - scheme: default",
        "      primary: deep purple",
        "      accent: amber",
        "      toggle:",
        "        icon: material/brightness-7",
        "        name: Switch to dark mode",
        "  font:",
        "    text: Inter",
        "    code: JetBrains Mono",
        "  features:",
        "    - navigation.instant",
        "    - navigation.tracking",
        "    - navigation.sections",
        "    - navigation.expand",
        "    - navigation.top",
        "    - search.suggest",
        "    - search.highlight",
        "    - content.code.copy",
        "    - content.code.annotate",
        "    - toc.follow",
        "",
        "markdown_extensions:",
        "  - pymdownx.highlight:",
        "      anchor_linenums: true",
        "      pygments_lang_class: true",
        "  - pymdownx.inlinehilite",
        "  - pymdownx.snippets",
        "  - pymdownx.superfences",
        "  - pymdownx.tabbed:",
        "      alternate_style: true",
        "  - pymdownx.details",
        "  - admonition",
        "  - tables",
        "  - toc:",
        "      permalink: true",
        "      toc_depth: 3",
        "  - attr_list",
        "  - md_in_html",
        "",
        "plugins:",
        "  - search",
        "",
        "nav:",
        "  - Home: index.md",
    ]
    for d in domains:
        lines.append(f"  - {title_case(d)}:")
        lines.append(f"      - Overview: {d}/index.md")

    return "\n".join(lines) + "\n"


def generate_index_md(name: str, domains: list[str]) -> str:
    domain_list = "\n".join(
        f"- **[{title_case(d)}]({d}/index.md)** - _(add description)_" for d in domains
    )
    return textwrap.dedent(f"""\
        # {title_case(name)} Knowledge Base

        Internal knowledge base for the **{title_case(name)}** project.
        Every module, decision, and gotcha in one place.

        ## How to use

        Claude Code sessions should consult this KB before implementing any module.

        1. **Before coding**: read the article for the module you are about to work on
        2. **Code from KB = canonical**: do not deviate without documenting why
        3. **After decisions**: update or create the relevant article
        4. **Gotchas are gold**: every bug, edge case, or surprise goes into Gotchas

        ## Domains

        {domain_list}

        ## Adding articles

        Use the article template: copy `_ARTICLE.md`, rename to `<topic>.md`,
        place in the correct domain folder, and add to `mkdocs.yml` nav.
    """)


def generate_domain_index(domain: str) -> str:
    return textwrap.dedent(f"""\
        # {title_case(domain)}

        Articles in this domain:

        _(Add links to articles as they are created)_
    """)


def generate_article_template() -> str:
    return textwrap.dedent("""\
        ---
        module: module-name
        status: draft
        owner: ""
        ---

        # Module Name

        What this module does and why it exists. 2-3 sentences.

        ## Public API

        ```cpp
        // Key types and functions exposed by this module
        ```

        ## Implementation Notes

        Key decisions and rationale. Why this approach, not alternatives.

        ## Code

        ```cpp
        // Core implementation - copy-paste ready, with comments
        ```

        ## Usage Example

        ```cpp
        // Minimal working example
        ```

        ## Gotchas

        - **Problem**: What can go wrong
        - **Why**: Root cause
        - **Fix**: How to resolve

        ## Dependencies

        - Related Module - how they connect
    """)


def generate_llms_txt(name: str, domains: list[str]) -> str:
    lines = [
        f"# {title_case(name)} Knowledge Base",
        "",
        f"> Internal knowledge base for the {title_case(name)} project.",
        "> Modules, decisions, API references, and gotchas.",
        "",
    ]
    for d in domains:
        lines.append(f"## {title_case(d)}")
        lines.append(f"- [{title_case(d)} Overview](/{d}/): Domain overview")
        lines.append("")
    return "\n".join(lines)


def generate_requirements() -> str:
    return textwrap.dedent("""\
        mkdocs>=1.6
        mkdocs-material>=9.5
    """)


def scaffold(name: str, domains: list[str], port: int, output: Path) -> None:
    slug = slugify(name)
    output = output or Path(f"{slug}-kb")

    if output.exists() and any(output.iterdir()):
        print(f"Error: {output} already exists and is not empty", file=sys.stderr)
        sys.exit(1)

    docs = output / "docs"
    docs.mkdir(parents=True, exist_ok=True)

    # mkdocs.yml
    (output / "mkdocs.yml").write_text(
        generate_mkdocs_yml(name, domains, port), encoding="utf-8"
    )

    # requirements.txt
    (output / "requirements.txt").write_text(
        generate_requirements(), encoding="utf-8"
    )

    # docs/index.md
    (docs / "index.md").write_text(
        generate_index_md(name, domains), encoding="utf-8"
    )

    # docs/_ARTICLE.md (template)
    (docs / "_ARTICLE.md").write_text(
        generate_article_template(), encoding="utf-8"
    )

    # docs/llms.txt
    (docs / "llms.txt").write_text(
        generate_llms_txt(name, domains), encoding="utf-8"
    )

    # Domain folders with index.md
    for d in domains:
        domain_dir = docs / d
        domain_dir.mkdir(exist_ok=True)
        (domain_dir / "index.md").write_text(
            generate_domain_index(d), encoding="utf-8"
        )

    print(f"Created {slug} KB at {output}/")
    print(f"  Domains: {', '.join(domains)}")
    print(f"  Port: {port}")
    print()
    print("Next steps:")
    print(f"  cd {output}")
    print(f"  pip install -r requirements.txt")
    print(f"  mkdocs serve -a 0.0.0.0:{port}")


def main():
    parser = argparse.ArgumentParser(
        description="Scaffold a Project Knowledge Base (MkDocs Material)"
    )
    parser.add_argument(
        "--name", required=True, help="Project name (e.g. 'retouch-app')"
    )
    parser.add_argument(
        "--domains",
        required=True,
        help="Comma-separated domain names (e.g. 'engine,converter,plugin')",
    )
    parser.add_argument(
        "--port", type=int, default=8200, help="MkDocs serve port (default: 8200)"
    )
    parser.add_argument(
        "--output", type=Path, default=None, help="Output directory (default: <name>-kb)"
    )
    args = parser.parse_args()

    domains = [slugify(d) for d in args.domains.split(",") if d.strip()]
    if not domains:
        print("Error: at least one domain required", file=sys.stderr)
        sys.exit(1)

    output = args.output or Path(f"{slugify(args.name)}-kb")
    scaffold(args.name, domains, args.port, output)


if __name__ == "__main__":
    main()
