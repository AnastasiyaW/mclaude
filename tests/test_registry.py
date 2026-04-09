"""Tests for mclaude.registry."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mclaude.registry import Identity, Registry


def test_identity_name_validation() -> None:
    # Valid
    Identity(name="ani")
    Identity(name="ani-1")
    Identity(name="ab")

    # Invalid
    with pytest.raises(ValueError):
        Identity(name="A")
    with pytest.raises(ValueError):
        Identity(name="with spaces")
    with pytest.raises(ValueError):
        Identity(name="-leading")
    with pytest.raises(ValueError):
        Identity(name="a")  # too short


def test_identity_auto_generates_id() -> None:
    i = Identity(name="ani")
    assert i.id.startswith("c0d3-ani-")
    assert len(i.id) > len("c0d3-ani-")


def test_register_and_get(tmp_path: Path) -> None:
    reg = Registry(project_root=tmp_path)
    ident = Identity(name="ani", owner="Anastasia", roles=["infra"])
    stored = reg.register(ident)
    assert stored.id == ident.id

    retrieved = reg.get("ani")
    assert retrieved is not None
    assert retrieved.name == "ani"
    assert retrieved.owner == "Anastasia"
    assert retrieved.roles == ["infra"]


def test_register_updates_existing(tmp_path: Path) -> None:
    reg = Registry(project_root=tmp_path)
    original = reg.register(Identity(name="ani", owner="Old owner"))
    original_id = original.id

    updated = Identity(name="ani", owner="New owner", roles=["backend"])
    reg.register(updated)

    # ID is preserved, but other fields change
    retrieved = reg.get("ani")
    assert retrieved is not None
    assert retrieved.id == original_id
    assert retrieved.owner == "New owner"
    assert retrieved.roles == ["backend"]


def test_list_all(tmp_path: Path) -> None:
    reg = Registry(project_root=tmp_path)
    reg.register(Identity(name="ani"))
    reg.register(Identity(name="vasya"))
    reg.register(Identity(name="team-bot"))

    all_ids = reg.list_all()
    names = {i.name for i in all_ids}
    assert names == {"ani", "vasya", "team-bot"}


def test_remove(tmp_path: Path) -> None:
    reg = Registry(project_root=tmp_path)
    reg.register(Identity(name="ani"))
    reg.register(Identity(name="vasya"))

    assert reg.remove("ani") is True
    assert reg.get("ani") is None
    assert reg.get("vasya") is not None

    assert reg.remove("does-not-exist") is False


def test_touch_updates_last_seen(tmp_path: Path) -> None:
    reg = Registry(project_root=tmp_path)
    reg.register(Identity(name="ani"))
    first = reg.get("ani").last_seen

    import time
    time.sleep(1.1)

    reg.touch("ani")
    second = reg.get("ani").last_seen
    assert second >= first


def test_whoami_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reg = Registry(project_root=tmp_path)
    reg.register(Identity(name="ani", owner="Anastasia"))

    monkeypatch.setenv("MCLAUDE_IDENTITY", "ani")
    me = reg.whoami()
    assert me is not None
    assert me.name == "ani"
    assert me.owner == "Anastasia"

    monkeypatch.delenv("MCLAUDE_IDENTITY")
    assert reg.whoami() is None


def test_notify_dict_persists(tmp_path: Path) -> None:
    reg = Registry(project_root=tmp_path)
    reg.register(Identity(
        name="ani",
        notify={"telegram_chat_id": "123", "email": "a@example.com"},
    ))
    retrieved = reg.get("ani")
    assert retrieved.notify["telegram_chat_id"] == "123"
    assert retrieved.notify["email"] == "a@example.com"
