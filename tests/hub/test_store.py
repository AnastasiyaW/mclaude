"""Unit tests for mclaude.hub.store - SQLite persistence layer."""
from __future__ import annotations

from mclaude.common.models import Event, EventType, IdentityInfo, LockClaim, SessionInfo
from mclaude.hub.store import Store


def test_create_and_get_project(store: Store) -> None:
    store.create_project("proj-1", "My Project")
    p = store.get_project("proj-1")
    assert p is not None
    assert p["id"] == "proj-1"
    assert p["name"] == "My Project"

    # Idempotent
    store.create_project("proj-1", "Ignored Rename")
    p2 = store.get_project("proj-1")
    assert p2["name"] == "My Project"


def test_register_and_list_identities(store: Store) -> None:
    store.create_project("proj-1", "P")
    store.register_identity(IdentityInfo(
        project_id="proj-1",
        name="ani",
        owner="Anastasia",
        roles=["infra", "ml"],
    ))
    store.register_identity(IdentityInfo(
        project_id="proj-1",
        name="vasya",
        owner="Vasily",
        roles=["frontend"],
    ))

    identities = store.list_identities("proj-1")
    names = {i.name for i in identities}
    assert names == {"ani", "vasya"}


def test_identity_upsert_preserves_id(store: Store) -> None:
    store.create_project("proj-1", "P")
    store.register_identity(IdentityInfo(project_id="proj-1", name="ani", owner="Old"))
    store.register_identity(IdentityInfo(project_id="proj-1", name="ani", owner="New", roles=["ml"]))
    identities = store.list_identities("proj-1")
    assert len(identities) == 1
    assert identities[0].owner == "New"
    assert identities[0].roles == ["ml"]


def test_session_lifecycle(store: Store) -> None:
    store.create_project("proj-1", "P")
    session = SessionInfo(id="s1", project_id="proj-1", identity="ani")
    store.start_session(session)
    active = store.list_active_sessions("proj-1")
    assert len(active) == 1
    assert active[0].id == "s1"

    store.heartbeat_session("s1")
    active = store.list_active_sessions("proj-1")
    assert active[0].status == "active"

    store.end_session("s1")
    active = store.list_active_sessions("proj-1")
    assert len(active) == 0


def test_lock_claim_is_atomic(store: Store) -> None:
    store.create_project("proj-1", "P")
    lock1 = LockClaim(
        project_id="proj-1",
        slug="fix-auth",
        session_id="s1",
        identity="ani",
        description="First claim",
    )
    assert store.claim_lock(lock1) is True

    # Second claim by different session should fail
    lock2 = LockClaim(
        project_id="proj-1",
        slug="fix-auth",
        session_id="s2",
        identity="vasya",
        description="Colliding claim",
    )
    assert store.claim_lock(lock2) is False

    active = store.list_active_locks("proj-1")
    assert len(active) == 1
    assert active[0].session_id == "s1"


def test_lock_release_only_by_owner(store: Store) -> None:
    store.create_project("proj-1", "P")
    lock = LockClaim(
        project_id="proj-1",
        slug="fix-auth",
        session_id="s1",
        identity="ani",
    )
    store.claim_lock(lock)

    # Wrong session cannot release
    assert store.release_lock("proj-1", "fix-auth", "s2") is False

    # Right session can
    assert store.release_lock("proj-1", "fix-auth", "s1") is True

    active = store.list_active_locks("proj-1")
    assert len(active) == 0


def test_lock_reclaim_after_release(store: Store) -> None:
    store.create_project("proj-1", "P")
    store.claim_lock(LockClaim(project_id="proj-1", slug="x", session_id="s1", identity="ani"))
    store.release_lock("proj-1", "x", "s1")
    # Should be able to reclaim same slug
    assert store.claim_lock(LockClaim(project_id="proj-1", slug="x", session_id="s2", identity="vasya")) is True


def test_events_insert_and_query(store: Store) -> None:
    store.create_project("proj-1", "P")
    event = Event(
        project_id="proj-1",
        type=EventType.MESSAGE_QUESTION,
        from_identity="ani",
        to_identity="vasya",
        subject="How to mock datetime",
        body="I want to freeze time",
    )
    store.insert_event(event)

    events = store.list_events("proj-1")
    assert len(events) == 1
    assert events[0].subject == "How to mock datetime"
    assert events[0].type == EventType.MESSAGE_QUESTION


def test_events_filter_by_recipient(store: Store) -> None:
    store.create_project("proj-1", "P")
    store.insert_event(Event(
        project_id="proj-1",
        type=EventType.MESSAGE_UPDATE,
        from_identity="ani",
        to_identity="vasya",
        subject="for vasya",
    ))
    store.insert_event(Event(
        project_id="proj-1",
        type=EventType.MESSAGE_UPDATE,
        from_identity="ani",
        to_identity="petya",
        subject="for petya",
    ))
    store.insert_event(Event(
        project_id="proj-1",
        type=EventType.MESSAGE_BROADCAST,
        from_identity="ani",
        to_identity="*",
        subject="to everyone",
    ))

    vasya_events = store.list_events("proj-1", to_identity="vasya")
    subjects = {e.subject for e in vasya_events}
    assert "for vasya" in subjects
    assert "to everyone" in subjects  # broadcast included
    assert "for petya" not in subjects
