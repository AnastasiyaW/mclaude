"""
Vikunja Bridge - connect mclaude locks/handoffs to an external task board.

This is an *adapter*, not a task system. The team task board lives in Vikunja
(or Linear, Jira, GitHub Projects, ...). mclaude handles the "while working"
coordination: who has the task claimed right now, what are they doing, what
did they finish.

Why a bridge instead of mirroring tasks into files?

  - Single source of truth. The team already curates priorities, assignees,
    and descriptions in Vikunja. Mirroring creates drift.
  - Human workflow preserved. Managers assign, comment, re-prioritise in the
    UI they already use. Claude sessions read from that UI, act, write back.
  - mclaude stays minimal. No task management engine, no scheduling. Just
    "I am working on Vikunja task #N, here is the lock, here is the beat,
    here is the handoff when done."

## What the bridge does

1. **Pull** - list open tasks assigned to a given identity (e.g. "ani-claude").
2. **Claim** - mark a Vikunja task in-progress AND take an mclaude lock on
   its slug. If the lock is already held, we refuse the claim (another
   session already started this task - probably on a different machine).
3. **Annotate** - push a link to the mclaude handoff back to Vikunja as
   a comment when the work finishes.
4. **Complete** - close the Vikunja task, release the lock, write handoff.

## What the bridge does NOT do

- It does not replicate task bodies locally. `claim()` returns the task
  dict once; if you need the body again, fetch it from Vikunja.
- It does not poll. Call `list_assigned()` when a session has capacity.
- It does not authenticate on behalf of users. Auth material is read from
  an environment variable the operator configures.

## Config

    {
      "url": "https://your-vikunja-instance.example.com",
      "auth_env": "VIKUNJA_AUTH",
      "identity_to_username": {
        "ani": "ani",
        "vasya": "vasily"
      }
    }

Stored at `.claude/vikunja.json`. Authentication material comes from an
environment variable so the config file stays commit-safe.

## Skeleton, not a full implementation

This file defines the interface and the integration points with mclaude's
lock and handoff layers. The actual Vikunja API calls are stubbed out
(marked TODO) because our team's Vikunja instance may need specific auth
headers, project IDs, and label conventions that are better tuned on site.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class VikunjaConfig:
    url: str
    auth: str   # opaque auth material; bridge does not inspect or store it
    identity_to_username: dict[str, str]

    @classmethod
    def load(cls, project_root: Path) -> "VikunjaConfig":
        cfg_path = project_root / ".claude" / "vikunja.json"
        if not cfg_path.exists():
            raise FileNotFoundError(f"No Vikunja config at {cfg_path}")
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        auth_env = raw.get("auth_env", "VIKUNJA_AUTH")
        auth = os.environ.get(auth_env, "")
        if not auth:
            raise RuntimeError(
                f"Vikunja auth not set. Export {auth_env} in the environment "
                f"before running, or change auth_env in {cfg_path}."
            )
        return cls(
            url=raw["url"].rstrip("/"),
            auth=auth,
            identity_to_username=raw.get("identity_to_username", {}),
        )


@dataclass
class VikunjaTask:
    id: int
    title: str
    description: str
    slug: str            # derived from title, used for mclaude lock
    assignee: str        # Vikunja username
    priority: int        # Vikunja priority (1-5)
    labels: list[str]
    project_id: int
    done: bool


def _request(cfg: VikunjaConfig, method: str, path: str,
             data: Optional[dict] = None) -> dict:
    """Minimal JSON client against the Vikunja REST API."""
    url = f"{cfg.url}/api/v1{path}"
    headers = {
        "Authorization": cfg.auth,  # whatever your instance expects
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _task_from_api(d: dict) -> VikunjaTask:
    import re
    title = d.get("title", "")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")[:60] or f"task-{d['id']}"
    return VikunjaTask(
        id=int(d["id"]),
        title=title,
        description=d.get("description", ""),
        slug=slug,
        assignee=(d.get("assignees") or [{}])[0].get("username", ""),
        priority=int(d.get("priority", 0)),
        labels=[lbl.get("title", "") for lbl in (d.get("labels") or [])],
        project_id=int(d.get("project_id", 0)),
        done=bool(d.get("done", False)),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_assigned(project_root: Path, identity: str,
                  *, project_id: Optional[int] = None) -> list[VikunjaTask]:
    """List open Vikunja tasks assigned to the identity's mapped username."""
    cfg = VikunjaConfig.load(project_root)
    username = cfg.identity_to_username.get(identity, identity)
    # Vikunja endpoint - adjust to your instance's filter syntax.
    # TODO(site-specific): your instance may expect different filter params.
    params = f"?filter_by=assignees&filter_value={username}&filter_by=done&filter_value=false"
    if project_id is not None:
        params += f"&filter_by=project_id&filter_value={project_id}"
    raw = _request(cfg, "GET", f"/tasks/all{params}")
    items = raw if isinstance(raw, list) else raw.get("data", [])
    return [_task_from_api(x) for x in items if not x.get("done")]


def claim(project_root: Path, task: VikunjaTask, identity: str) -> dict:
    """Claim a Vikunja task AND take an mclaude lock on its slug.

    Flow:
      1. Try to take the lock first (local, cheap, fails fast if busy).
      2. Comment on Vikunja task that it is being worked on.
      3. Return info the caller uses in handoff.

    If the lock is held by another session, we do NOT touch Vikunja -
    that would create a misleading "in progress" state while the real
    work is being done elsewhere.
    """
    from . import locks
    try:
        lock_path = locks.claim(
            project_root, task.slug, identity,
            description=f"vikunja:{task.id} {task.title}",
        )
    except Exception as e:
        raise RuntimeError(
            f"Cannot claim Vikunja #{task.id} - mclaude lock for '{task.slug}' "
            f"is already held. Probably another session started this task."
        ) from e

    cfg = VikunjaConfig.load(project_root)
    comment = {
        "comment": f"Claimed by {identity} (mclaude session). "
                   f"Lock: `{task.slug}`",
    }
    try:
        _request(cfg, "PUT", f"/tasks/{task.id}/comments", data=comment)
    except Exception as e:
        # If the comment fails we keep the lock - the work is happening,
        # just the annotation did not land. User can add it manually.
        print(f"[vikunja] warning: failed to comment on #{task.id}: {e}")

    return {
        "vikunja_task_id": task.id,
        "slug": task.slug,
        "lock_path": str(lock_path),
    }


def complete(project_root: Path, task: VikunjaTask, identity: str,
             *, handoff_path: Optional[Path] = None,
             release_lock: bool = True) -> dict:
    """Close the Vikunja task and release the mclaude lock."""
    cfg = VikunjaConfig.load(project_root)
    body = {"done": True}
    _request(cfg, "POST", f"/tasks/{task.id}", data=body)

    if handoff_path is not None:
        # Add a comment linking to the handoff file
        note = f"Done by {identity}. Handoff: `{handoff_path}`"
        try:
            _request(cfg, "PUT", f"/tasks/{task.id}/comments",
                     data={"comment": note})
        except Exception as e:
            print(f"[vikunja] warning: could not post done-comment: {e}")

    if release_lock:
        from . import locks
        try:
            locks.release(project_root, task.slug)
        except Exception as e:
            print(f"[vikunja] warning: lock release failed for {task.slug}: {e}")

    return {"vikunja_task_id": task.id, "closed": True}
