"""
Cross-team channel: talk to another person's Claude through a shared Vikunja project.

mclaude's own messages live in `.claude/messages/` and only reach sessions that
share the same disk (or the same hub). A partner in another company has neither.
What both sides usually *can* share is a task tracker. This module turns one
Vikunja project into a mailbox between two parties:

- one topic = one task, titled `[<from> -> <to>] <topic>`;
- every letter = one comment on that task (HTML, with an @-mention of the reader);
- the task is assigned to whoever has to act next, so it shows up in their
  "assigned to me" list and in Vikunja's notifications.

Nothing here is imported by the rest of mclaude; the core stays zero-dependency
and tracker-agnostic. This module itself is stdlib only.

Configuration lives in `.claude/mclaude-channel.json` (no secrets). The API
token is read from the `MCLAUDE_CHANNEL_TOKEN` environment variable or from a
file named in the config (`token_file`), and is never written by mclaude.

    from mclaude.channel import Channel

    ch = Channel.from_project(".")
    ch.send("Data export format", "Can you send a sample CSV?")
    for letter in ch.inbox():
        print(letter.task_id, letter.author, letter.text)
"""
from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

CONFIG_REL = Path(".claude") / "mclaude-channel.json"
STATE_REL = Path(".claude") / "messages" / ".channel_state.json"
TOKEN_ENV = "MCLAUDE_CHANNEL_TOKEN"

# Must match the identity part of mclaude.messages.FILENAME_RE so pulled
# letters can be stored as regular mclaude messages.
IDENTITY_RE = re.compile(r"^[a-zA-Z0-9-]+$")
TITLE_RE = re.compile(r"^\[\s*([^\]]+?)\s*->\s*([^\]]+?)\s*\]\s*(.*)$")

PERMISSIONS = {"read": 0, "write": 1, "admin": 2}

# simplification: at most this many task pages (50 per page) are scanned per
# call; a dedicated partner project stays far below that. Use the tracker's
# filter API if a project ever grows past it.
MAX_TASK_PAGES = 20
PER_PAGE = 50


class ChannelError(Exception):
    """A request failed or the channel is misconfigured."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# -- configuration -----------------------------------------------------------


@dataclass
class ChannelConfig:
    base_url: str  # API base, ends with /api/v1
    project_id: int
    me: str  # short identity of this side, e.g. "acme"
    peer: str  # short identity of the other side, e.g. "anya"
    me_username: str  # Vikunja login of this side
    peer_username: str  # Vikunja login of the other side
    token_file: str = ""  # optional path to a file holding only the token

    def __post_init__(self) -> None:
        self.base_url = normalize_base_url(self.base_url)
        self.project_id = int(self.project_id)
        for name in ("me", "peer"):
            value = getattr(self, name)
            if not IDENTITY_RE.match(value or ""):
                raise ChannelError(f"{name}={value!r}: use letters, digits and '-' only")
        if not self.me_username or not self.peer_username:
            raise ChannelError("me_username and peer_username are required")

    @classmethod
    def load(cls, root: str | Path) -> ChannelConfig:
        path = Path(root) / CONFIG_REL
        if not path.is_file():
            raise ChannelError(f"no channel configured: {path} is missing (run `mclaude channel setup`)")
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, root: str | Path) -> Path:
        path = Path(root) / CONFIG_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def token(self) -> str:
        value = os.environ.get(TOKEN_ENV, "").strip()
        if value:
            return value
        if self.token_file:
            path = Path(os.path.expanduser(self.token_file))
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
            raise ChannelError(f"token_file {path} does not exist")
        raise ChannelError(f"no API token: set {TOKEN_ENV} or token_file in {CONFIG_REL.as_posix()}")


def normalize_base_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ChannelError(f"base_url must start with http:// or https://, got {url!r}")
    if not url.endswith("/api/v1"):
        url += "/api/v1"
    return url


# -- transport ---------------------------------------------------------------


class VikunjaClient:
    """Minimal JSON client. Errors become ChannelError with the HTTP status."""

    def __init__(self, base_url: str, token: str, timeout: float = 20.0) -> None:
        self.base_url = normalize_base_url(base_url)
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, payload: Any = None, auth: bool = True) -> Any:
        headers = {"Accept": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise ChannelError(f"{method} {path} -> HTTP {exc.code} {detail}", status=exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ChannelError(f"{method} {path} -> network error: {exc}") from None
        return json.loads(body) if body.strip() else None


# -- formatting --------------------------------------------------------------


def title_for(sender: str, recipient: str, topic: str) -> str:
    return f"[{sender} -> {recipient}] {topic.strip()}"


def parse_title(title: str) -> tuple[str, str, str] | None:
    """`[a -> b] topic` -> ("a", "b", "topic"); None for other titles."""
    m = TITLE_RE.match(title or "")
    return (m.group(1), m.group(2), m.group(3).strip()) if m else None


def text_to_html(text: str) -> str:
    """Plain text / light markdown -> safe HTML. ``` fences become <pre><code>."""
    out: list[str] = []
    para: list[str] = []
    code: list[str] | None = None

    def flush_para() -> None:
        if para:
            out.append("<p>" + "<br>".join(html.escape(line) for line in para) + "</p>")
            para.clear()

    for line in text.replace("\r\n", "\n").split("\n"):
        if line.strip().startswith("```"):
            if code is None:
                flush_para()
                code = []
            else:
                out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code = None
            continue
        if code is not None:
            code.append(line)
        elif line.strip():
            para.append(line)
        else:
            flush_para()
    if code is not None:
        out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    flush_para()
    return "\n".join(out)


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "li", "pre", "h1", "h2", "h3", "h4", "tr", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK and tag != "br":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup or "")
    text = "".join(parser.parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# -- channel -----------------------------------------------------------------


@dataclass
class Letter:
    """One incoming item: a comment, or the description of a new task."""

    task_id: int
    topic: str
    author: str
    created: str
    text: str
    comment_id: int | None = None  # None = the task description


@dataclass
class Check:
    name: str
    state: str  # PASS | FAIL | UNKNOWN
    detail: str = ""


@dataclass
class Channel:
    config: ChannelConfig
    client: Any  # anything with .request(method, path, payload=None, auth=True)
    root: Path = field(default_factory=Path.cwd)
    partial: bool = field(default=False, init=False)  # last inbox() hit its deadline

    @classmethod
    def from_project(cls, root: str | Path = ".", timeout: float = 20.0) -> Channel:
        config = ChannelConfig.load(root)
        return cls(config, VikunjaClient(config.base_url, config.token(), timeout), Path(root))

    # -- low level -------------------------------------------------------

    def _api(self, method: str, path: str, payload: Any = None) -> Any:
        return self.client.request(method, path, payload)

    def tasks(self, include_done: bool = False) -> list[dict]:
        out: list[dict] = []
        for page in range(1, MAX_TASK_PAGES + 1):
            batch = self._api(
                "GET",
                f"/projects/{self.config.project_id}/tasks"
                f"?page={page}&per_page={PER_PAGE}&sort_by=updated&order_by=desc",
            ) or []
            out.extend(batch)
            if len(batch) < PER_PAGE:
                break
        return out if include_done else [t for t in out if not t.get("done")]

    def comments(self, task_id: int) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            batch = self._api("GET", f"/tasks/{task_id}/comments?page={page}&per_page={PER_PAGE}") or []
            out.extend(batch)
            if len(batch) < PER_PAGE:
                return out
            page += 1

    def user_id(self, username: str) -> int:
        """Resolve a login to an id from the instance itself, never from memory."""
        try:
            for u in self._api("GET", f"/projects/{self.config.project_id}/projectusers") or []:
                if u.get("username") == username:
                    return int(u["id"])
        except ChannelError:
            pass
        try:
            for u in self._api("GET", f"/users?s={urllib.request.quote(username)}") or []:
                if u.get("username") == username:
                    return int(u["id"])
        except ChannelError:
            pass
        # Scoped API tokens often may not list users; the same identities are
        # visible on the project's own tasks and comments.
        for task in self.tasks(include_done=True):
            people = [task.get("created_by") or {}, *(task.get("assignees") or [])]
            for u in people:
                if u.get("username") == username and u.get("id"):
                    return int(u["id"])
        raise ChannelError(
            f"cannot resolve Vikunja user {username!r}: not in project users, user search, "
            "or any task of this project (give the token projects_users: read_all, "
            "or let that user open the first thread)",
            status=404,
        )

    # -- sending ---------------------------------------------------------

    def find_thread(self, topic: str) -> dict | None:
        """Open task about `topic` between the two sides, in either direction."""
        pair = {self.config.me, self.config.peer}
        wanted = topic.strip().casefold()
        for task in self.tasks():
            parsed = parse_title(task.get("title", ""))
            if parsed and {parsed[0], parsed[1]} == pair and parsed[2].casefold() == wanted:
                return task
        return None

    def open_thread(self, topic: str) -> dict:
        title = title_for(self.config.me, self.config.peer, topic)
        return self._api("PUT", f"/projects/{self.config.project_id}/tasks", {"title": title})

    def assign(self, task_id: int, username: str) -> None:
        """Assign and verify by reading the task back (a 201 is not proof)."""
        task = self._api("GET", f"/tasks/{task_id}")
        if any(u.get("username") == username for u in task.get("assignees") or []):
            return
        self._api("PUT", f"/tasks/{task_id}/assignees", {"user_id": self.user_id(username)})
        task = self._api("GET", f"/tasks/{task_id}")
        if not any(u.get("username") == username for u in task.get("assignees") or []):
            raise ChannelError(f"task {task_id}: assignee {username!r} did not stick")

    def comment(self, task_id: int, text: str) -> dict:
        """Post a letter, mention the reader, and verify it reads back intact."""
        body = f"<p>@{html.escape(self.config.peer_username)}</p>\n" + text_to_html(text)
        created = self._api("PUT", f"/tasks/{task_id}/comments", {"comment": body})
        cid = int(created["id"])
        stored = next((c for c in self.comments(task_id) if int(c.get("id", -1)) == cid), None)
        if stored is None:
            raise ChannelError(f"task {task_id}: comment {cid} not found on read-back")
        expected = html_to_text(text_to_html(text))
        if html_to_text(stored.get("comment", "")).find(expected[:200]) < 0:
            raise ChannelError(f"task {task_id}: comment {cid} text differs on read-back (encoding?)")
        return stored

    def send(self, topic: str, text: str, task_id: int | None = None) -> tuple[int, int]:
        """Send a letter on `topic`; reuse the open thread if there is one.

        Returns (task_id, comment_id). The thread is (re)assigned to the peer:
        the ball is in their court now.
        """
        if task_id is None:
            task_id = int((self.find_thread(topic) or self.open_thread(topic))["id"])
        stored = self.comment(task_id, text)
        try:
            self.assign(task_id, self.config.peer_username)
        except ChannelError as exc:
            raise ChannelError(
                f"letter delivered (task {task_id}, comment {stored['id']}) but assigning "
                f"{self.config.peer_username!r} failed: {exc}", exc.status) from None
        return task_id, int(stored["id"])

    def close(self, task_id: int) -> None:
        # Vikunja's update replaces the whole task, so send it back complete.
        task = self._api("GET", f"/tasks/{task_id}")
        task["done"] = True
        self._api("POST", f"/tasks/{task_id}", task)
        if not self._api("GET", f"/tasks/{task_id}").get("done"):
            raise ChannelError(f"task {task_id} is still open after update")

    def invite(self, username: str, permission: str = "write") -> None:
        """Host side: share this project with the partner's account."""
        if permission not in PERMISSIONS:
            raise ChannelError(f"permission must be one of {sorted(PERMISSIONS)}")
        try:
            self._api(
                "PUT",
                f"/projects/{self.config.project_id}/users",
                {"username": username, "permission": PERMISSIONS[permission]},
            )
        except ChannelError as exc:
            if exc.status != 409:
                raise

    # -- receiving -------------------------------------------------------

    def _state_path(self) -> Path:
        return self.root / STATE_REL

    def _load_state(self) -> dict:
        try:
            return json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"tasks": {}}

    def _save_state(self, state: dict) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def inbox(self, mark: bool = True, deadline: float | None = None) -> list[Letter]:
        """New letters from the peer since the last call.

        `deadline` (time.monotonic() value) bounds the scan; when it is hit
        the result is partial and `self.partial` is True.
        """
        self.partial = False
        state = self._load_state()
        seen_tasks = state.setdefault("tasks", {})
        letters: list[Letter] = []
        for task in self.tasks():
            if deadline is not None and time.monotonic() > deadline:
                self.partial = True
                break
            tid = int(task["id"])
            entry = seen_tasks.setdefault(str(tid), {})
            if entry.get("updated") == task.get("updated"):
                continue
            topic = (parse_title(task.get("title", "")) or ("", "", task.get("title", "")))[2]
            author = (task.get("created_by") or {}).get("username", "")
            if not entry.get("description_seen") and author != self.config.me_username:
                text = html_to_text(task.get("description", ""))
                if text:
                    letters.append(Letter(tid, topic, author, task.get("created", ""), text))
            entry["description_seen"] = True
            last = int(entry.get("seen_comment", 0))
            for c in self.comments(tid):
                cid = int(c.get("id", 0))
                if cid <= last:
                    continue
                who = (c.get("author") or {}).get("username", "")
                if who != self.config.me_username:
                    letters.append(Letter(tid, topic, who, c.get("created", ""), html_to_text(c.get("comment", "")), cid))
                entry["seen_comment"] = max(int(entry.get("seen_comment", 0)), cid)
            entry["updated"] = task.get("updated")
        if mark:
            self._save_state(state)
        return letters

    def pull(self, store: Any = None, deadline: float | None = None) -> list[Path]:
        """Copy new letters into the local mclaude inbox (shown by the hooks)."""
        from .messages import Message, MessageStore

        store = store or MessageStore(self.root)
        written: list[Path] = []
        for letter in self.inbox(mark=True, deadline=deadline):
            footer = (
                f"\n\n---\nVikunja task {letter.task_id}. Reply: "
                f"`mclaude channel reply {letter.task_id} --body-file <file>`"
            )
            written.append(store.send(Message(
                from_=self.config.peer,
                to=self.config.me,
                type="update",
                subject=letter.topic or f"task {letter.task_id}",
                thread=f"vikunja-{letter.task_id}",
                body=letter.text + footer,
            )))
        return written

    # -- diagnostics -----------------------------------------------------

    def doctor(self) -> list[Check]:
        """Check every capability the channel needs. Three states, not two."""
        checks: list[Check] = []

        def run(name: str, fn: Callable[[], str]) -> bool:
            try:
                checks.append(Check(name, "PASS", fn() or ""))
                return True
            except ChannelError as exc:
                state = "FAIL" if exc.status else "UNKNOWN"
                checks.append(Check(name, state, str(exc)))
                return False

        def info() -> str:
            data = self.client.request("GET", "/info", auth=False) or {}
            return f"Vikunja {data.get('version', '?')}"

        if not run("server reachable", info):
            return checks
        if not run("project readable (projects: read_one)",
                   lambda: self._api("GET", f"/projects/{self.config.project_id}").get("title", "")):
            return checks
        tasks: list[dict] = []

        def list_tasks() -> str:
            tasks.extend(self.tasks(include_done=True))
            return f"{len(tasks)} task(s)"

        run("tasks listable (tasks: read_all)", list_tasks)
        if tasks:
            tid = int(tasks[0]["id"])
            run("comments readable (tasks_comments: read_all)", lambda: f"{len(self.comments(tid))} on task {tid}")
        else:
            checks.append(Check("comments readable (tasks_comments: read_all)", "UNKNOWN",
                                "no task yet; send a first letter to test"))
        run(f"peer {self.config.peer_username!r} resolvable",
            lambda: f"id {self.user_id(self.config.peer_username)}")
        return checks
