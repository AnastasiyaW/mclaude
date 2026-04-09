"""
mclaude CLI - unified entry point for all four layers.

Subcommands:

    mclaude lock claim --slug <s> --description "..." [--files a.py b.py]
    mclaude lock release <slug> [--summary "..."]
    mclaude lock status <slug>
    mclaude lock list
    mclaude lock heartbeat <slug>
    mclaude lock force-release <slug> --reason "..."

    mclaude handoff write [--goal "..." --slug "..."]
    mclaude handoff list [--status ACTIVE|CLOSED|...]
    mclaude handoff read <filename-or-slug>
    mclaude handoff latest

    mclaude memory save --wing <w> --room <r> --hall <h> --title "..." --content "..."
    mclaude memory search <query> [--wing <w>]
    mclaude memory list [--wing <w> --room <r>]
    mclaude memory core        # print the L0+L1 always-loaded file

    mclaude identity register <name> --owner "..."
    mclaude identity list
    mclaude identity whoami
    mclaude identity remove <name>

For fuller docs, see README.md or docs/protocol.md.
"""
from __future__ import annotations

import argparse
import sys

from . import handoffs as _handoffs
from . import locks as _locks
from . import memory as _memory
from . import registry as _registry


def _add_lock_parser(sub: argparse._SubParsersAction) -> None:
    lock = sub.add_parser("lock", help="Atomic work claims")
    lock_sub = lock.add_subparsers(dest="lock_cmd", required=True)

    # Reuse the standalone lock parser from mclaude.locks
    # Build a minimal bridge - just forward to _locks.build_parser's commands.
    inner = _locks.build_parser()
    for action in inner._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, subparser in action.choices.items():
                lock_sub.add_parser(
                    name,
                    parents=[subparser],
                    add_help=False,
                    description=subparser.description,
                )
    lock.set_defaults(_dispatch="lock")


def _add_handoff_parser(sub: argparse._SubParsersAction) -> None:
    h = sub.add_parser("handoff", help="Session handoffs")
    h_sub = h.add_subparsers(dest="handoff_cmd", required=True)

    write = h_sub.add_parser("write", help="Write a new handoff")
    write.add_argument("--session", required=True, help="Your session ID")
    write.add_argument("--goal", required=True, help="Goal of the session")
    write.add_argument("--slug", help="Override the auto-generated slug")
    write.add_argument("--done", nargs="*", default=[], help="What was done")
    write.add_argument("--not-worked", nargs="*", default=[], help="What failed (with reason)")
    write.add_argument("--working", nargs="*", default=[], help="Verified working")
    write.add_argument("--broken", nargs="*", default=[], help="Currently broken")
    write.add_argument("--blocked", nargs="*", default=[], help="External blockers")
    write.add_argument("--next-step", default="", help="Single concrete next action")

    list_cmd = h_sub.add_parser("list", help="List handoff files")
    list_cmd.add_argument("--status", help="Filter by status (ACTIVE, CLOSED, etc)")

    read_cmd = h_sub.add_parser("read", help="Read a handoff file")
    read_cmd.add_argument("target", help="Filename or slug fragment")

    h_sub.add_parser("latest", help="Print the latest handoff")

    h.set_defaults(_dispatch="handoff")


def _add_memory_parser(sub: argparse._SubParsersAction) -> None:
    m = sub.add_parser("memory", help="Knowledge graph")
    m_sub = m.add_subparsers(dest="memory_cmd", required=True)

    save = m_sub.add_parser("save", help="Save a drawer")
    save.add_argument("--wing", required=True)
    save.add_argument("--room", required=True)
    save.add_argument("--hall", default="facts", help="decisions|gotchas|references|discoveries|preferences|facts")
    save.add_argument("--title", required=True)
    save.add_argument("--content", required=True, help="Raw verbatim text (mandatory)")
    save.add_argument("--session", default="", help="Session ID of the writer")
    save.add_argument("--tags", nargs="*", default=[])

    search = m_sub.add_parser("search", help="Substring search (grep baseline)")
    search.add_argument("query")
    search.add_argument("--wing", help="Limit to one wing")

    lst = m_sub.add_parser("list", help="List drawers in the graph")
    lst.add_argument("--wing")
    lst.add_argument("--room")
    lst.add_argument("--hall")
    lst.add_argument("--include-superseded", action="store_true")

    m_sub.add_parser("core", help="Print the L0+L1 always-loaded core memory")

    m.set_defaults(_dispatch="memory")


def _add_identity_parser(sub: argparse._SubParsersAction) -> None:
    ident = sub.add_parser("identity", help="Identity registry")
    i_sub = ident.add_subparsers(dest="identity_cmd", required=True)

    reg = i_sub.add_parser("register", help="Register a new identity")
    reg.add_argument("name")
    reg.add_argument("--owner", default="")
    reg.add_argument("--machine", default="")
    reg.add_argument("--roles", nargs="*", default=[])
    reg.add_argument("--notify", nargs="*", default=[], help="key:value pairs, e.g. telegram:123")

    i_sub.add_parser("list", help="List all identities")
    i_sub.add_parser("whoami", help="Print current identity (from MCLAUDE_IDENTITY env var)")

    rm = i_sub.add_parser("remove", help="Remove an identity")
    rm.add_argument("name")

    ident.set_defaults(_dispatch="identity")


def build_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mclaude",
        description="Multi-session collaboration layer for Claude Code agents.",
    )
    p.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    sub = p.add_subparsers(dest="command", required=True)

    _add_lock_parser(sub)
    _add_handoff_parser(sub)
    _add_memory_parser(sub)
    _add_identity_parser(sub)

    return p


# -- Dispatch ---------------------------------------------------------------

def _dispatch_lock(args: argparse.Namespace) -> int:
    # Rebuild via the original lock parser for consistent handling
    # Extract the inner command name and args
    lock_cmd = args.lock_cmd
    # Re-run through the locks module
    sys.argv = ["project_lock.py", lock_cmd] + _flatten_known_lock_args(args, lock_cmd)
    return _locks.main()


def _flatten_known_lock_args(args: argparse.Namespace, lock_cmd: str) -> list[str]:
    out: list[str] = []
    for k, v in vars(args).items():
        if k.startswith("_") or k in ("command", "lock_cmd"):
            continue
        if v is None or v is False:
            continue
        if isinstance(v, bool):
            out.append(f"--{k.replace('_', '-')}")
        elif isinstance(v, list):
            if v:
                out.extend([f"--{k.replace('_', '-')}", *[str(x) for x in v]])
        else:
            # positional vs optional: locks puts slug as positional for some commands
            if k == "slug" and lock_cmd in ("heartbeat", "status", "release", "force-release"):
                out.append(str(v))
            else:
                out.extend([f"--{k.replace('_', '-')}", str(v)])
    return out


def _dispatch_handoff(args: argparse.Namespace) -> int:
    store = _handoffs.HandoffStore()
    cmd = args.handoff_cmd
    if cmd == "write":
        h = _handoffs.Handoff(
            session_id=args.session,
            goal=args.goal,
            done=args.done,
            not_worked=args.not_worked,
            working=args.working,
            broken=args.broken,
            blocked=args.blocked,
            next_step=args.next_step,
            slug_override=args.slug,
        )
        path = store.write(h)
        print(f"[handoff] written {path}")
        return 0
    if cmd == "list":
        lines = store.get_index_lines(status_filter=args.status)
        if not lines:
            print("[handoff] index is empty")
            return 0
        print("\n".join(lines))
        return 0
    if cmd == "read":
        matches = store.find_by_slug(args.target)
        if not matches:
            print(f"[handoff] no handoff matching {args.target!r}")
            return 1
        if len(matches) > 1:
            print("[handoff] multiple matches, showing newest:")
            for m in matches[:5]:
                print(f"  {m.name}")
            print()
        print(matches[0].read_text(encoding="utf-8"))
        return 0
    if cmd == "latest":
        latest = store.latest()
        if not latest:
            print("[handoff] no handoffs recorded yet")
            return 1
        print(latest.read_text(encoding="utf-8"))
        return 0
    return 1


def _dispatch_memory(args: argparse.Namespace) -> int:
    graph = _memory.MemoryGraph()
    cmd = args.memory_cmd
    if cmd == "save":
        drawer = _memory.Drawer(
            title=args.title,
            content=args.content,
            hall=args.hall,
            session_id=args.session,
            tags=args.tags,
        )
        path = graph.save(args.wing, args.room, drawer)
        print(f"[memory] saved {path}")
        return 0
    if cmd == "search":
        results = graph.search(args.query, wing=args.wing)
        if not results:
            print("[memory] no matches")
            return 0
        for path, line in results:
            rel = path.relative_to(graph.root)
            print(f"{rel}: {line}")
        return 0
    if cmd == "list":
        drawers = graph.list_drawers(
            wing=args.wing,
            room=args.room,
            hall=args.hall,
            include_superseded=args.include_superseded,
        )
        if not drawers:
            print("[memory] no drawers found")
            return 0
        for path in drawers:
            rel = path.relative_to(graph.root)
            print(rel)
        return 0
    if cmd == "core":
        print(graph.read_core())
        return 0
    return 1


def _dispatch_identity(args: argparse.Namespace) -> int:
    reg = _registry.Registry()
    cmd = args.identity_cmd
    if cmd == "register":
        notify = {}
        for pair in args.notify:
            if ":" in pair:
                k, v = pair.split(":", 1)
                notify[k] = v
        identity = _registry.Identity(
            name=args.name,
            owner=args.owner,
            machine=args.machine,
            roles=args.roles,
            notify=notify,
        )
        stored = reg.register(identity)
        print(f"[identity] registered {stored.name} (id={stored.id})")
        return 0
    if cmd == "list":
        for ident in reg.list_all():
            print(f"{ident.name:20} id={ident.id:32} owner={ident.owner or '-':20} last_seen={ident.last_seen}")
        return 0
    if cmd == "whoami":
        me = reg.whoami()
        if not me:
            print("[identity] MCLAUDE_IDENTITY not set, no current identity")
            return 1
        print(f"{me.name} id={me.id} owner={me.owner} roles={','.join(me.roles)}")
        return 0
    if cmd == "remove":
        ok = reg.remove(args.name)
        print(f"[identity] {'removed' if ok else 'not found'}: {args.name}")
        return 0 if ok else 1
    return 1


def main() -> int:
    parser = build_cli()
    args = parser.parse_args()
    dispatch = getattr(args, "_dispatch", None)
    if dispatch == "lock":
        return _dispatch_lock(args)
    if dispatch == "handoff":
        return _dispatch_handoff(args)
    if dispatch == "memory":
        return _dispatch_memory(args)
    if dispatch == "identity":
        return _dispatch_identity(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
