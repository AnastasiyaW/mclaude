"""
FastAPI hub server.

Minimal REST + WebSocket relay. Authentication is via bearer tokens configured
in `hub.toml` (or passed to `create_app` in tests). Every request must include
`Authorization: Bearer <token>`; the token identifies the project and identity.

The server is designed to be **import-testable**: `create_app()` returns a
FastAPI instance without starting uvicorn, so tests can wrap it in
`fastapi.testclient.TestClient` directly.

Run in production:

    uvicorn mclaude_hub.hub.server:create_app --factory --host 0.0.0.0 --port 8080

Or from Python:

    from mclaude.hub.server import create_app
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=8080)
"""
# NOTE: we deliberately do NOT use `from __future__ import annotations` here.
# FastAPI introspects type annotations at route registration time using
# eval_type_lenient(). Stringified annotations (which PEP 563 forces when
# __future__ annotations is active) break the dependency resolver: it sees
# `token: "Annotated[TokenInfo, Depends(authorize)]"` as a string, fails to
# parse it as a Depends marker, and treats `token` as a required query
# parameter. Keeping annotations concrete fixes the 422 errors.
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from mclaude.common.models import Event, EventType, IdentityInfo, LockClaim, SessionInfo
from mclaude.hub.store import Store


# -- Auth config --------------------------------------------------------------

@dataclass
class TokenInfo:
    """What a bearer token authorizes."""

    project_id: str
    identity: str


@dataclass
class HubConfig:
    """Runtime configuration for the hub server."""

    db_path: str = "mclaude_hub.db"
    # Map of bearer_token -> TokenInfo
    tokens: dict[str, TokenInfo] = field(default_factory=dict)
    # If True, allow unauthenticated requests (test mode only)
    allow_anonymous: bool = False


# -- Pydantic request/response models ---------------------------------------

class ProjectCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1)


class IdentityCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=32)
    owner: str = ""
    machine: str = ""
    roles: list[str] = Field(default_factory=list)
    notify: dict = Field(default_factory=dict)


class SessionStart(BaseModel):
    identity: str
    machine: str = ""


class LockClaimRequest(BaseModel):
    slug: str = Field(..., min_length=3, max_length=80)
    session_id: str
    identity: str
    description: str = ""
    files: list[str] = Field(default_factory=list)


class EventCreate(BaseModel):
    type: str  # EventType value
    from_identity: str
    to_identity: str = "*"
    subject: str = ""
    body: str = ""
    urgent: bool = False
    thread: str | None = None
    reply_to: str | None = None
    session_id: str | None = None


# -- WebSocket hub (broadcast fan-out) ---------------------------------------

class Broadcaster:
    """In-memory fan-out for WebSocket connections, scoped to project.

    A single server instance holds a map of project_id -> set of active
    WebSocket connections. When an event arrives, it is pushed to every
    connection in the matching project's set.

    Thread/async safety: FastAPI's default single-process model means we
    only need an asyncio.Lock for the connection set, not a threading.Lock.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, project_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._subscriptions.setdefault(project_id, set()).add(ws)

    async def unsubscribe(self, project_id: str, ws: WebSocket) -> None:
        async with self._lock:
            subs = self._subscriptions.get(project_id)
            if subs:
                subs.discard(ws)
                if not subs:
                    self._subscriptions.pop(project_id, None)

    async def publish(self, project_id: str, payload: dict) -> None:
        """Send payload to every subscriber. Dead connections are removed."""
        async with self._lock:
            subs = list(self._subscriptions.get(project_id, ()))
        dead: list[WebSocket] = []
        for ws in subs:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    subs = self._subscriptions.get(project_id)
                    if subs:
                        subs.discard(ws)


# -- App factory -------------------------------------------------------------

def create_app(config: HubConfig | None = None) -> FastAPI:
    """Factory that returns a FastAPI app.

    Tests pass a HubConfig with `allow_anonymous=True` and a `:memory:` store.
    Production loads the config from `hub.toml`.
    """
    cfg = config or HubConfig()
    store = Store(cfg.db_path)
    broadcaster = Broadcaster()

    app = FastAPI(title="mclaude-hub", version="0.1.0")

    def authorize(authorization: Annotated[str | None, Header()] = None) -> TokenInfo:
        """Dependency that extracts and validates the bearer token."""
        if cfg.allow_anonymous:
            # Test / dev mode - accept without a token, return a synthetic identity
            return TokenInfo(project_id="default", identity="anonymous")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token = authorization[len("Bearer "):].strip()
        info = cfg.tokens.get(token)
        if not info:
            raise HTTPException(status_code=401, detail="Invalid bearer token")
        return info

    # -- Health check ----------------------------------------------------

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": "0.1.0"}

    # -- Projects --------------------------------------------------------

    @app.post("/api/projects", status_code=201)
    async def create_project(
        project: ProjectCreate,
        token: Annotated[TokenInfo, Depends(authorize)],
    ) -> dict:
        store.create_project(project.id, project.name)
        return {"id": project.id, "name": project.name}

    @app.get("/api/projects/{project_id}")
    async def get_project(
        project_id: str,
        token: Annotated[TokenInfo, Depends(authorize)],
    ) -> dict:
        p = store.get_project(project_id)
        if not p:
            raise HTTPException(status_code=404, detail="Project not found")
        return p

    # -- Identities ------------------------------------------------------

    @app.post("/api/identities", status_code=201)
    async def register_identity(
        payload: IdentityCreate,
        token: Annotated[TokenInfo, Depends(authorize)],
    ) -> dict:
        identity = IdentityInfo(
            project_id=token.project_id,
            name=payload.name,
            owner=payload.owner,
            machine=payload.machine,
            roles=payload.roles,
            notify=payload.notify,
        )
        store.register_identity(identity)
        return identity.to_dict()

    @app.get("/api/identities")
    async def list_identities(
        token: Annotated[TokenInfo, Depends(authorize)],
    ) -> list[dict]:
        return [i.to_dict() for i in store.list_identities(token.project_id)]

    # -- Sessions --------------------------------------------------------

    @app.post("/api/sessions", status_code=201)
    async def start_session(
        payload: SessionStart,
        token: Annotated[TokenInfo, Depends(authorize)],
    ) -> dict:
        import uuid as _uuid
        session = SessionInfo(
            id=_uuid.uuid4().hex,
            project_id=token.project_id,
            identity=payload.identity,
            machine=payload.machine,
        )
        store.start_session(session)
        event = Event(
            project_id=token.project_id,
            type=EventType.SESSION_STARTED,
            from_identity=payload.identity,
            to_identity="*",
            subject=f"Session started by {payload.identity}",
            session_id=session.id,
        )
        store.insert_event(event)
        await broadcaster.publish(token.project_id, event.to_dict())
        return session.to_dict()

    @app.post("/api/sessions/{session_id}/heartbeat")
    async def heartbeat_session(
        session_id: str,
        token: Annotated[TokenInfo, Depends(authorize)],
    ) -> dict:
        store.heartbeat_session(session_id)
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/end")
    async def end_session(
        session_id: str,
        token: Annotated[TokenInfo, Depends(authorize)],
    ) -> dict:
        store.end_session(session_id)
        event = Event(
            project_id=token.project_id,
            type=EventType.SESSION_ENDED,
            from_identity=token.identity,
            session_id=session_id,
            subject="Session ended",
        )
        store.insert_event(event)
        await broadcaster.publish(token.project_id, event.to_dict())
        return {"ok": True}

    @app.get("/api/sessions")
    async def list_sessions(
        token: Annotated[TokenInfo, Depends(authorize)],
    ) -> list[dict]:
        return [s.to_dict() for s in store.list_active_sessions(token.project_id)]

    # -- Locks -----------------------------------------------------------

    @app.post("/api/locks/claim")
    async def claim_lock(
        payload: LockClaimRequest,
        token: Annotated[TokenInfo, Depends(authorize)],
    ) -> dict:
        lock = LockClaim(
            project_id=token.project_id,
            slug=payload.slug,
            session_id=payload.session_id,
            identity=payload.identity,
            description=payload.description,
            files=payload.files,
        )
        claimed = store.claim_lock(lock)
        if not claimed:
            existing = store.get_active_lock(token.project_id, payload.slug)
            raise HTTPException(
                status_code=409,
                detail={"error": "already_held", "lock": existing.to_dict() if existing else None},
            )
        event = Event(
            project_id=token.project_id,
            type=EventType.LOCK_CLAIMED,
            from_identity=payload.identity,
            subject=f"Lock claimed: {payload.slug}",
            body=payload.description,
            session_id=payload.session_id,
        )
        store.insert_event(event)
        await broadcaster.publish(token.project_id, event.to_dict())
        return lock.to_dict()

    @app.post("/api/locks/{slug}/release")
    async def release_lock(
        slug: str,
        session_id: str,
        token: Annotated[TokenInfo, Depends(authorize)],
    ) -> dict:
        ok = store.release_lock(token.project_id, slug, session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Lock not found or not owned by this session")
        event = Event(
            project_id=token.project_id,
            type=EventType.LOCK_RELEASED,
            from_identity=token.identity,
            subject=f"Lock released: {slug}",
            session_id=session_id,
        )
        store.insert_event(event)
        await broadcaster.publish(token.project_id, event.to_dict())
        return {"ok": True}

    @app.get("/api/locks")
    async def list_locks(
        token: Annotated[TokenInfo, Depends(authorize)],
    ) -> list[dict]:
        return [l.to_dict() for l in store.list_active_locks(token.project_id)]

    # -- Events (including messages) ------------------------------------

    @app.post("/api/events", status_code=201)
    async def create_event(
        payload: EventCreate,
        token: Annotated[TokenInfo, Depends(authorize)],
    ) -> dict:
        try:
            event_type = EventType(payload.type)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid event type: {payload.type}")
        event = Event(
            project_id=token.project_id,
            type=event_type,
            from_identity=payload.from_identity,
            to_identity=payload.to_identity,
            subject=payload.subject,
            body=payload.body,
            urgent=payload.urgent,
            thread=payload.thread,
            reply_to=payload.reply_to,
            session_id=payload.session_id,
        )
        store.insert_event(event)
        await broadcaster.publish(token.project_id, event.to_dict())
        return event.to_dict()

    @app.get("/api/events")
    async def list_events(
        token: Annotated[TokenInfo, Depends(authorize)],
        to_identity: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        events = store.list_events(
            project_id=token.project_id,
            to_identity=to_identity,
            since=since,
            limit=limit,
        )
        return [e.to_dict() for e in events]

    # -- WebSocket stream ------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        """Subscribe to all events for a project.

        Client must send an auth message after connecting:
            {"token": "<bearer>", "project_id": "<project>"}

        If the token is valid, the connection is added to the broadcaster
        and will receive all subsequent events as JSON messages.
        """
        await ws.accept()
        try:
            first = await ws.receive_text()
            auth = json.loads(first)
        except Exception:
            await ws.send_json({"error": "invalid_auth_handshake"})
            await ws.close()
            return

        if cfg.allow_anonymous:
            project_id = auth.get("project_id", "default")
        else:
            token = auth.get("token", "")
            info = cfg.tokens.get(token)
            if not info:
                await ws.send_json({"error": "invalid_token"})
                await ws.close()
                return
            project_id = info.project_id

        await broadcaster.subscribe(project_id, ws)
        await ws.send_json({"status": "subscribed", "project_id": project_id})

        try:
            while True:
                # We mostly push to the client; any incoming text is a noop ping.
                msg = await ws.receive_text()
                if msg == "ping":
                    await ws.send_text("pong")
        except WebSocketDisconnect:
            pass
        finally:
            await broadcaster.unsubscribe(project_id, ws)

    # Attach components for tests
    app.state.store = store
    app.state.broadcaster = broadcaster
    app.state.config = cfg

    return app
