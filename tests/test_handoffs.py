"""Tests for mclaude.handoffs."""
from __future__ import annotations

from pathlib import Path

from mclaude.handoffs import Handoff, HandoffStore, slugify


def test_slugify_basic() -> None:
    assert slugify("Fix the drift validator bug") == "fix-drift-validator-bug"
    assert slugify("Test Multi-Session Collaboration Layer") == "test-multi-session-collaboration-layer"
    assert slugify("") == "untitled-work"
    assert slugify("the and or of") == "the-and-or-of"  # all stopwords - fallback to raw
    assert slugify("A very long sentence with too many words to fit", max_words=3) == "very-long-sentence"


def test_handoff_filename_format() -> None:
    h = Handoff(
        session_id="373d1618abcdef",
        goal="Fix the drift validator bug",
        timestamp="2026-04-09_14-32",
    )
    assert h.session_short() == "373d1618"
    assert h.filename() == "2026-04-09_14-32_373d1618_fix-drift-validator-bug.md"


def test_handoff_iso_timestamp_normalization() -> None:
    h = Handoff(
        session_id="abcdef1234",
        goal="Test ISO timestamp",
        timestamp="2026-04-09T14:32:00",
    )
    # Should accept ISO and normalize to YYYY-MM-DD_HH-MM
    assert h.filename().startswith("2026-04-09_14-32_")


def test_handoff_slug_override() -> None:
    h = Handoff(
        session_id="abcdef12",
        goal="Something totally different",
        slug_override="custom-name-here",
        timestamp="2026-04-09_15-00",
    )
    assert "custom-name-here" in h.filename()
    assert "totally-different" not in h.filename()


def test_handoff_render_has_all_sections() -> None:
    h = Handoff(
        session_id="abcd1234",
        goal="Test rendering",
        done=["item 1", "item 2"],
        not_worked=["tried X, failed because Y"],
        working=["feature A"],
        broken=["feature B with error Z"],
        decisions=[("chose X", "because Y")],
        next_step="do the next thing",
    )
    md = h.render_markdown()
    assert "# Session Handoff" in md
    assert "## Goal" in md
    assert "## Done" in md
    assert "## What did NOT work" in md
    assert "## Current state" in md
    assert "## Key decisions" in md
    assert "## Next step" in md
    assert "chose X" in md
    assert "because Y" in md
    assert "tried X, failed because Y" in md


def test_handoff_store_write_creates_file_and_index(tmp_path: Path) -> None:
    store = HandoffStore(project_root=tmp_path)
    h = Handoff(
        session_id="test1234",
        goal="Store test",
        done=["wrote test"],
        next_step="run it",
        timestamp="2026-04-09_10-00",
    )
    path = store.write(h)
    assert path.exists()
    assert path.name == "2026-04-09_10-00_test1234_store-test.md"
    assert store.index_path.exists()
    index_content = store.index_path.read_text(encoding="utf-8")
    assert "store-test" in index_content
    assert "ACTIVE" in index_content


def test_handoff_no_overwrite_on_name_collision(tmp_path: Path) -> None:
    """Two handoffs that happen to get the same name should not collide."""
    store = HandoffStore(project_root=tmp_path)
    h1 = Handoff(session_id="same1234", goal="Collision test", timestamp="2026-04-09_10-00")
    h2 = Handoff(session_id="same1234", goal="Collision test", timestamp="2026-04-09_10-00")
    p1 = store.write(h1)
    p2 = store.write(h2)
    assert p1 != p2
    assert p1.exists()
    assert p2.exists()
    assert "_2" in p2.name


def test_handoff_list_and_latest(tmp_path: Path) -> None:
    store = HandoffStore(project_root=tmp_path)
    h_old = Handoff(session_id="old12345", goal="Old work", timestamp="2026-04-08_10-00")
    h_new = Handoff(session_id="new12345", goal="New work", timestamp="2026-04-09_10-00")
    store.write(h_old)
    store.write(h_new)

    all_files = store.list_all()
    assert len(all_files) == 2
    # Sorted newest first
    assert "2026-04-09" in all_files[0].name

    latest = store.latest()
    assert latest is not None
    assert "new12345" in latest.name


def test_handoff_find_by_slug_fragment(tmp_path: Path) -> None:
    store = HandoffStore(project_root=tmp_path)
    store.write(Handoff(session_id="abc12345", goal="Auth middleware fix"))
    store.write(Handoff(session_id="def12345", goal="Frontend dashboard"))

    matches = store.find_by_slug("auth")
    assert len(matches) == 1
    assert "auth" in matches[0].name
