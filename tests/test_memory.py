"""Tests for mclaude.memory."""
from __future__ import annotations

from pathlib import Path

from mclaude.memory import Drawer, MemoryGraph


def test_save_and_list(tmp_path: Path) -> None:
    g = MemoryGraph(project_root=tmp_path)
    d = Drawer(
        title="Use JWT not sessions",
        content="Decision: JWT 15min access + 30day refresh...",
        hall="decisions",
        tags=["auth", "architecture"],
    )
    path = g.save(wing="myapp", room="auth-system", drawer=d)
    assert path.exists()
    assert "decisions" in str(path)
    assert "use-jwt-not-sessions" in path.name

    drawers = g.list_drawers(wing="myapp")
    assert len(drawers) == 1
    assert drawers[0] == path


def test_supersede_marks_old_and_creates_new(tmp_path: Path) -> None:
    g = MemoryGraph(project_root=tmp_path)
    old = Drawer(title="Use JWT", content="Original decision", hall="decisions")
    old_path = g.save(wing="myapp", room="auth", drawer=old)

    new = Drawer(title="Use OAuth instead", content="We changed our minds after X", hall="decisions")
    old_updated, new_path = g.supersede(old_path, new)

    # Old file still exists but is marked superseded
    assert old_updated.exists()
    old_content = old_updated.read_text(encoding="utf-8")
    assert "superseded_by: 2026" in old_content or "superseded_by: " in old_content
    # At least the null should be gone
    assert "superseded_by: null" not in old_content
    assert "valid_to: null" not in old_content

    # New file exists
    assert new_path.exists()


def test_list_excludes_superseded_by_default(tmp_path: Path) -> None:
    g = MemoryGraph(project_root=tmp_path)
    old = Drawer(title="Decision A", content="first", hall="decisions")
    old_path = g.save(wing="myapp", room="design", drawer=old)
    new = Drawer(title="Decision A v2", content="second", hall="decisions")
    g.supersede(old_path, new)

    # By default superseded are hidden
    active = g.list_drawers(wing="myapp")
    assert len(active) == 1
    assert "v2" in active[0].name

    # With flag, both appear
    everything = g.list_drawers(wing="myapp", include_superseded=True)
    assert len(everything) == 2


def test_search_finds_content(tmp_path: Path) -> None:
    g = MemoryGraph(project_root=tmp_path)
    g.save(
        wing="myapp", room="auth",
        drawer=Drawer(title="JWT decision", content="We chose JWT for stateless auth"),
    )
    g.save(
        wing="myapp", room="db",
        drawer=Drawer(title="Postgres", content="We chose Postgres for durability"),
    )

    jwt_results = g.search("JWT")
    assert len(jwt_results) >= 1

    pg_results = g.search("Postgres")
    assert len(pg_results) >= 1

    none_results = g.search("MongoDB")
    assert len(none_results) == 0


def test_core_file_created_on_ensure(tmp_path: Path) -> None:
    g = MemoryGraph(project_root=tmp_path)
    g.ensure()
    assert g.core_path.exists()
    content = g.read_core()
    assert "L0" in content
    assert "L1" in content


def test_filter_by_wing_room_hall(tmp_path: Path) -> None:
    g = MemoryGraph(project_root=tmp_path)
    g.save(
        wing="app1", room="auth",
        drawer=Drawer(title="one", content="a", hall="decisions"),
    )
    g.save(
        wing="app1", room="auth",
        drawer=Drawer(title="two", content="b", hall="gotchas"),
    )
    g.save(
        wing="app2", room="auth",
        drawer=Drawer(title="three", content="c", hall="decisions"),
    )

    app1 = g.list_drawers(wing="app1")
    assert len(app1) == 2

    app1_decisions = g.list_drawers(wing="app1", hall="decisions")
    assert len(app1_decisions) == 1

    app2 = g.list_drawers(wing="app2")
    assert len(app2) == 1
