# Pattern Index

Lookup table for all pattern files in this directory. Check here before starting any task — if a pattern exists, follow it.

| Pattern | Use when |
|---------|----------|
| [add-cli-verb.md](add-cli-verb.md) | Adding or extending a `tkt` verb or flag in `core/cli.py` — includes the argparse trap that silently eats `--config`/`--json` |
| [add-provider-adapter.md](add-provider-adapter.md) | Adding a new ticketing backend: implement the verb contract, register it, ship an example config |
| [debug-config-resolution.md](debug-config-resolution.md) | A command exits 2 or 4 — wrong config file picked up, unmapped role, undefined query, missing key |
| [debug-run-loop.md](debug-run-loop.md) | `tkt run` is stuck, halting early, retrying forever, or resuming from the wrong phase |
| [edit-skill-pack.md](edit-skill-pack.md) | Changing a skill or subagent — portability rules, which harness translations to regenerate, and what reaches consumer repos |
