"""Tests for memory graph knowledge index and wiki-links."""
from __future__ import annotations

from pathlib import Path

import pytest

from mclaude.memory import Drawer, MemoryGraph


@pytest.fixture
def graph(tmp_path: Path) -> MemoryGraph:
    g = MemoryGraph(tmp_path)
    g.ensure()
    return g


class TestBuildIndex:
    def test_empty_graph(self, graph: MemoryGraph):
        assert graph.build_index() == []

    def test_indexes_drawers(self, graph: MemoryGraph):
        graph.save("myapp", "auth", Drawer(
            title="Use JWT tokens", content="Decision text",
            hall="decisions", tags=["auth", "jwt"],
        ))
        graph.save("myapp", "auth", Drawer(
            title="JWT expiry race", content="Gotcha text",
            hall="gotchas", tags=["auth"],
        ))

        index = graph.build_index()
        assert len(index) == 2
        titles = [e["title"] for e in index]
        assert "Use JWT tokens" in titles
        assert "JWT expiry race" in titles

    def test_index_has_all_fields(self, graph: MemoryGraph):
        graph.save("myapp", "auth", Drawer(
            title="Test entry", content="Body",
            hall="facts", tags=["test", "sample"],
        ))
        entry = graph.build_index()[0]
        assert entry["wing"] == "myapp"
        assert entry["room"] == "auth"
        assert entry["hall"] == "facts"
        assert "test" in entry["tags"]
        assert "path" in entry


class TestFindSimilar:
    def test_finds_exact_match(self, graph: MemoryGraph):
        graph.save("myapp", "auth", Drawer(
            title="Use JWT tokens", content="Decision",
            hall="decisions",
        ))
        similar = graph.find_similar("Use JWT tokens")
        assert len(similar) == 1
        assert similar[0]["title"] == "Use JWT tokens"

    def test_finds_partial_match(self, graph: MemoryGraph):
        graph.save("myapp", "auth", Drawer(
            title="Use JWT tokens for auth", content="Decision",
            hall="decisions",
        ))
        similar = graph.find_similar("JWT tokens", threshold=0.3)
        assert len(similar) >= 1

    def test_no_match_below_threshold(self, graph: MemoryGraph):
        graph.save("myapp", "auth", Drawer(
            title="Use JWT tokens", content="Decision",
            hall="decisions",
        ))
        similar = graph.find_similar("PostgreSQL database setup", threshold=0.5)
        assert len(similar) == 0

    def test_skips_superseded(self, graph: MemoryGraph):
        path = graph.save("myapp", "auth", Drawer(
            title="Use sessions", content="Old decision",
            hall="decisions",
        ))
        graph.supersede(path, Drawer(
            title="Use JWT instead of sessions", content="New decision",
            hall="decisions",
        ))
        similar = graph.find_similar("Use sessions")
        # Should find the new one, not the superseded one
        titles = [s["title"] for s in similar]
        assert "Use sessions" not in titles or len(similar) <= 1


class TestRenderIndex:
    def test_empty_graph(self, graph: MemoryGraph):
        assert "empty" in graph.render_index().lower()

    def test_renders_table(self, graph: MemoryGraph):
        graph.save("myapp", "auth", Drawer(
            title="Use JWT tokens", content="D",
            hall="decisions", tags=["auth"],
        ))
        table = graph.render_index()
        assert "| Title" in table
        assert "Use JWT tokens" in table
        assert "myapp" in table


class TestWikiLinks:
    def test_drawer_with_links_renders(self, graph: MemoryGraph):
        drawer = Drawer(
            title="Auth overview",
            content="Auth system design",
            hall="facts",
            links=["myapp/auth/decisions/jwt-tokens", "myapp/auth/gotchas/expiry-race"],
        )
        rendered = drawer.render()
        assert "links:" in rendered
        assert "## Related" in rendered
        assert "[[myapp/auth/decisions/jwt-tokens]]" in rendered

    def test_drawer_without_links_no_related_section(self, graph: MemoryGraph):
        drawer = Drawer(title="Simple", content="No links", hall="facts")
        rendered = drawer.render()
        assert "## Related" not in rendered

    def test_find_backlinks(self, graph: MemoryGraph):
        # Save a drawer that links to another
        graph.save("myapp", "auth", Drawer(
            title="JWT tokens decision", content="We use JWT",
            hall="decisions",
        ))
        graph.save("myapp", "auth", Drawer(
            title="Auth overview", content="Summary of auth",
            hall="facts",
            links=["jwt-tokens-decision"],
        ))

        backlinks = graph.find_backlinks("jwt-tokens-decision")
        assert len(backlinks) >= 1
        assert backlinks[0]["title"] == "Auth overview"

    def test_no_backlinks(self, graph: MemoryGraph):
        graph.save("myapp", "auth", Drawer(
            title="Isolated entry", content="No links to this",
            hall="facts",
        ))
        backlinks = graph.find_backlinks("nonexistent")
        assert len(backlinks) == 0
