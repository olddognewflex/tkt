"""Argument parsing + verb dispatch. Default output is human-readable; `--json`
on read verbs emits the normalized shape for skills to parse."""
import argparse
import json
import sys

from .config import Config
from .errors import TktError, UsageError
from .registry import get_adapter
from .schema import Ticket


def _print_ticket_human(t: Ticket) -> None:
    print(f"{t.key}  [{t.type}/{t.type_class}]  {t.priority}")
    print(f"  status:   {t.status}  (role: {t.status_role})")
    print(f"  assignee: {t.assignee or '-'}")
    print(f"  summary:  {t.summary}")
    if t.labels:
        print(f"  labels:   {', '.join(t.labels)}")
    unresolved = t.unresolved_blockers()
    if unresolved:
        print(f"  BLOCKED BY: {', '.join(b['key'] for b in unresolved)}")
    if t.transitions:
        print(f"  next:     {', '.join(t.transitions)}")
    if t.url:
        print(f"  url:      {t.url}")


def _print_ticket_list(tickets: list[Ticket], as_json: bool) -> None:
    if as_json:
        print(json.dumps([t.to_dict() for t in tickets], indent=2))
        return
    if not tickets:
        print("(no tickets)")
        return
    for t in tickets:
        blocked = " [BLOCKED]" if t.unresolved_blockers() else ""
        print(f"{t.key}  {t.priority:<8} {t.status:<16} {t.summary}{blocked}")


def build_parser() -> argparse.ArgumentParser:
    # Shared flags usable on either side of the verb (tkt --json view X == tkt view X --json).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="path to .sdlc/config.toml (else auto-discover)")
    common.add_argument("--json", action="store_true", help="emit JSON where supported")

    p = argparse.ArgumentParser(
        prog="tkt", description="Provider-agnostic ticketing CLI", parents=[common]
    )
    sub = p.add_subparsers(dest="verb", required=True)

    def add(name):
        return sub.add_parser(name, parents=[common])

    add("whoami")

    sp = add("list")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--tier", type=int)
    g.add_argument("--query")

    sp = add("view")
    sp.add_argument("key")

    sp = add("transition")
    sp.add_argument("key")
    sp.add_argument("role")

    sp = add("comment")
    sp.add_argument("key")
    sp.add_argument("body")

    sp = add("blockers")
    sp.add_argument("key")

    sp = add("worklog")
    sp.add_argument("key")
    sp.add_argument("--from-role", required=True, dest="from_role")
    sp.add_argument("--note", default="")
    sp.add_argument("--billable", action="store_true")

    sp = add("lane-time")
    sp.add_argument("key")
    sp.add_argument("--role", required=True)

    sp = add("lane")
    sp.add_argument("role")

    sp = add("cfg")
    sp.add_argument("key", help="dotted config path, e.g. build.test or vcs.repo")
    sp.add_argument("--pkg", default="", help="substitute {pkg} in the value")
    sp.add_argument("--ticket", default="", help="substitute {key}/{key-lower}")
    sp.add_argument("--slug", default="", help="substitute {slug}")

    add("doctor")

    return p


def _subst(value: str, pkg: str, ticket: str, slug: str) -> str:
    return (value
            .replace("{pkg}", pkg)
            .replace("{key-lower}", ticket.lower())
            .replace("{key}", ticket)
            .replace("{slug}", slug))


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        config = Config.load(args.config)

        # `lane` and `cfg` are pure config resolution — no adapter/backend needed.
        if args.verb == "lane":
            print(config.role_to_lane(args.role))
            return 0

        if args.verb == "cfg":
            val = config.get(args.key)
            if isinstance(val, str):
                print(_subst(val, args.pkg, args.ticket, args.slug))
            else:
                print(json.dumps(val) if args.json else val)
            return 0

        adapter = get_adapter(config)

        if args.verb == "whoami":
            print(adapter.whoami())

        elif args.verb == "list":
            tickets = adapter.list(tier=args.tier, query=args.query)
            _print_ticket_list(tickets, args.json)

        elif args.verb == "view":
            t = adapter.view(args.key)
            if args.json:
                print(json.dumps(t.to_dict(), indent=2))
            else:
                _print_ticket_human(t)

        elif args.verb == "transition":
            adapter.transition(args.key, args.role)
            lane = config.role_to_lane(args.role)
            print(f"{args.key} -> {lane}")

        elif args.verb == "comment":
            adapter.comment(args.key, args.body)
            print(f"commented on {args.key}")

        elif args.verb == "blockers":
            blk = adapter.blockers(args.key)
            if args.json:
                print(json.dumps(blk, indent=2))
            elif not blk:
                print("(no unresolved blockers)")
            else:
                for b in blk:
                    print(f"{b['key']}  (unresolved)")

        elif args.verb == "worklog":
            wl = adapter.worklog(
                args.key, args.from_role, note=args.note, billable=args.billable
            )
            if args.json:
                print(json.dumps(wl.to_dict(), indent=2))
            else:
                where = wl.worklog_id or "(no time tracking)"
                print(f"{wl.key}  {wl.role}: {wl.human}  worklog={where}")

        elif args.verb == "lane-time":
            wl = adapter.lane_time(args.key, args.role)
            if args.json:
                print(json.dumps(wl.to_dict(), indent=2))
            else:
                where = wl.worklog_id or "(no time tracking)"
                print(f"{wl.key}  {wl.role}: {wl.human}  worklog={where}")

        elif args.verb == "doctor":
            checks = adapter.doctor()
            ok_all = all(c.ok for c in checks)
            if args.json:
                print(json.dumps([c.to_dict() for c in checks], indent=2))
            else:
                for c in checks:
                    mark = "ok " if c.ok else "FAIL"
                    print(f"[{mark}] {c.name}" + (f" — {c.detail}" if c.detail else ""))
            return 0 if ok_all else 1

        else:
            raise UsageError(f"unknown verb: {args.verb}")

        return 0

    except TktError as e:
        print(f"tkt: {e}", file=sys.stderr)
        return e.exit_code
    except KeyboardInterrupt:
        return 130
