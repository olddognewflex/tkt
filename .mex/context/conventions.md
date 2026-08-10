---
name: conventions
description: How code is written in this project — naming, structure, patterns, and style. Load when writing new code or reviewing existing code.
triggers:
  - "convention"
  - "pattern"
  - "naming"
  - "style"
  - "how should I"
  - "what's the right way"
edges:
  - target: context/architecture.md
    condition: when a convention depends on understanding the system structure
  - target: context/adapter-contract.md
    condition: when the code being written lives in adapters/ and must satisfy the verb contract
  - target: context/setup.md
    condition: when running the Verify Checklist and you need the exact commands
  - target: patterns/INDEX.md
    condition: when the work matches a known task type with its own runbook
grounds_to:
  - node: "method:a212dd164490894706bed1d2e89cb4d6"
    fingerprint: "mh:64:7b226d696e68617368223a5b31373636313031392c363734323636342c32313532313238312c35373132303635362c3132343331303137302c37363839323333362c33333037353636302c39383638323037392c37353636373238362c33383737303831322c31333034313534362c31313236353438362c34323234323235342c31383537323237312c36393238333634342c32373732313037342c3131383537303232312c323435333931342c35303631363336382c36373836343735362c32313830333839382c37333436353130332c3136383038313338352c3238363135343235322c3134353437393235372c34393338373230332c3330333735373032382c35313532323032362c39303431353236382c34333636393037392c33313736313835342c3134363731353938312c343431393134332c32333430313839382c38383736343531392c37383336363339332c3131303731353836362c35373534303936322c35363936323936392c38353631363238312c36353530383733302c3138303730343537392c3832363536312c37343832333037332c3231383237333332392c393232333838372c3139323232343838362c3231343830333238382c34393236353938322c3132303632313938342c3131353835363030372c3137363036323238312c3238343730393039372c3530373536362c38353336313636392c37383932363934362c35343139343937332c353534323930302c34353038383235312c37333335383338332c323035363330362c32393939383334352c32333032383630312c31313732363932355d2c226e65696768626f7273223a5b2266756e6374696f6e3a3331343734626432333962656463323566353135656161623732366134343932222c226d6574686f643a3031316130396237666138333461303633643361366165666163323136313037222c226d6574686f643a3038313733346434316439326135333461383330373335346633363631666439222c226d6574686f643a3163366536303530613637626263373638656231656164656137326463643334222c226d6574686f643a3231663362636463643237376337356338346430393831306364616639306132222c226d6574686f643a3363656463343262316334336636366437343261363030313438336233336339222c226d6574686f643a3464393036316366663164313861623132346530613734323266333836613238222c226d6574686f643a3530613765383666666432613165313135346231666664636630626562306463222c226d6574686f643a3563616261623531313030653365336639636639663664666334356165336362222c226d6574686f643a3630306539316634393934376230646539353032306264623938303264616535222c226d6574686f643a3635666236613763653531306465306635663864653931373636303939613736222c226d6574686f643a3638663462633036326631303131376537313166306530376338343033393538222c226d6574686f643a6139346435613739363633616139323165636439353132386332336530383938222c226d6574686f643a6230346266663065663566656463363830313166626534346232623038316439222c226d6574686f643a6266333133326462373332383562333864313338336665306132326237366461222c226d6574686f643a6330386166646539356564353262666239333137623233373634666163386637222c226d6574686f643a6439366639663865343066396262366336616262323366323432616338353665222c226d6574686f643a6466633533343761663464643034643431303236373838333539613033386339225d2c22746f6b656e436f756e74223a37317d"
  - node: "function:b9fbc395d2c9b3e3b608e02a514319a2"
    fingerprint: "mh:64:7b226d696e68617368223a5b31373636313031392c38353030383030312c33303336373738352c3130343439313630302c35363430333136382c393635343732332c32323736393730352c31333530393139342c36343935373937372c3230343835383035342c31333034313534362c3533343432392c34323234323235342c31383537323237312c373633323832392c32373732313037342c32353938313634392c38363137383938382c3138323338343139342c3133313830343636332c35383138323739332c35323831333334352c35373734393838302c3134303230313838332c37343135333236392c3135313232353033362c3133313838313734322c3135393731383333322c35383735303930382c33383431383937362c31333138303738312c3136323438373436362c35353037333938302c32333430313839382c38363738393535342c31343335383532302c39373632303736372c34363433343636352c35363936323936392c353731383634332c32313136363535352c3230343536363336332c39303031373634392c37343832333037332c383930363633302c31353737343834342c34343133393830302c37363234323634312c3135303730303839302c3132303632313938342c31343333363634382c37333535363435332c3238343730393039372c34323731323235342c32313437343538332c31393438363037312c35343139343937332c32343036353337372c38333630383837312c37333335383338332c323035363330362c32393939383334352c36303739383536372c31313732363932355d2c226e65696768626f7273223a5b2266756e6374696f6e3a3331343734626432333962656463323566353135656161623732366134343932225d2c22746f6b656e436f756e74223a36337d"
  - node: "method:da98cb8b89986009045295bfee04d4db"
    fingerprint: "mh:64:7b226d696e68617368223a5b39343930313136302c3232313237393333302c3138303633393536382c3136383931343633302c3132343331303137302c3333353732383230392c3135313431343531372c3130333434373737352c37353636373238362c3336323938343734332c3139393131333630382c31313236353438362c34323234323235342c31383537323237312c32363435373333382c32373732313037342c3131383537303232312c3135343639383639352c3234393732383131342c38303934333634362c37333737393438322c37333436353130332c3136383038313338352c3335303633393533392c3134353437393235372c38353335393234352c3131323834353136342c31303537373234392c3130373439343036382c3337353531393738352c33313736313835342c3134363731353938312c343431393134332c32333430313839382c38383736343531392c3330383931343138352c3131303731353836362c36303238383134312c35363936323936392c33323738313937302c3136363733313937322c3232363332303130372c3832363536312c3237363238323336382c3238313539363735332c393232333838372c3236333335383736332c3238333133393338372c3132323930393536302c32323934333537332c3131343131343137342c35393638393230342c3238343730393039372c3235353939363632322c38353336313636392c37383932363934362c35343139343937332c39333531313938342c383538363932332c34333538303432382c3133363233363736332c3136303734363933302c333235313338392c31313732363932355d2c226e65696768626f7273223a5b5d2c22746f6b656e436f756e74223a3131377d"
last_updated: 2026-08-04
---

# Conventions

## Naming

- **Roles, never lane names, in anything above an adapter.** The canonical roles are
  `backlog`, `todo`, `in_progress`, `review`, `qa_ready`, `qa`, `deploy_ready`, `done`,
  plus `revise`, `blocked`, `cancelled`. A literal lane string ("In Review") may only
  appear inside a provider's config or an adapter.
  [`Config.role_to_lane()`](mex://method:a212dd164490894706bed1d2e89cb4d6) is the one
  translation point, and it passes a valid lane name through unchanged.
- **Modules are lowercase single words** (`cli.py`, `pack.py`, `ticketdoc.py`,
  `toolchain.py`), one per responsibility. Adapters are named for their provider and
  match the registry key exactly: key `github` → `adapters/github.py` → `GithubAdapter`.
- **A leading underscore means module-private and untested-by-contract**
  (`_splice_block`, `_seed_build`, `_to_ticket`, `_acli`). Public names are the verb
  surface plus the handful of helpers the CLI imports.
- **Verb names are semantic, not backend words** — `transition`, `worklog`, `blockers`,
  `lane-time`. Never name anything after a provider concept (no `issue`, no `card`).
- **CLI flags are `--kebab-case` with an explicit `dest`** when they differ
  (`--from-role` → `from_role`, `--agent-status` → `agent_status`).

## Structure

- **`core/` must never import an adapter.** The only crossing point is
  `core/registry.py`, and it imports lazily inside the function so a broken adapter
  cannot take the process down at startup. Adapters import freely from `core/`.
- **Verbs that need no backend are dispatched before the adapter is built.** `init`
  and `sync-pack` run before `Config.load()` at all (they may run before `.sdlc/`
  exists); `lane` and `cfg` run after config load but construct no adapter. Adding a
  verb in the wrong tier makes it fail on a project that has no backend reachable.
- **Input validation lives in `core/cli.py`, not in adapters.** A bad date or an
  invalid `--agent-status` must fail before any network call, in one place, for every
  provider — see [`_validate_agent_status()`](mex://function:b9fbc395d2c9b3e3b608e02a514319a2).
- **Every adapter returns `core/schema.py` dataclasses**, never dicts and never
  provider payloads. A field the backend cannot store keeps its dataclass default;
  do not synthesize a plausible value.
- **Failures raise a typed `TktError` subclass; nothing returns an error sentinel.**
  `ConfigError` (2), `ProviderError` (3), `NotFoundError` (4), `UsageError` (64).
  `main()` catches `TktError` centrally, prints to stderr, and returns `exit_code`.
- **Non-obvious code carries a comment explaining the trap, not the mechanics.**
  The argparse `SUPPRESS` block and the `read_ticket_marker` fallback are the models
  to imitate: they explain what silently breaks if you "simplify" them.

## Patterns

**1. Tri-state optional fields: omit = unchanged, `""` = clear, value = set.**
This is the whole reason `edit`'s parameters default to `None` rather than `""`. Any
new editable field must follow it, in both the parser and
[`Adapter.edit()`](mex://method:da98cb8b89986009045295bfee04d4db).

```python
# Correct — argparse side
sp.add_argument("--assignee", default=None)   # None = not supplied

# Correct — adapter side
if assignee is not None:        # "" is a real value: clear the field
    fm["assignee"] = assignee

# Wrong — collapses "clear it" and "leave it alone" into one case
if assignee:
    fm["assignee"] = assignee
```

**2. Optional verbs opt in; they never break instantiation.** Mandatory verbs are
`@abstractmethod` on `Adapter`. `create`, `apply`, `edit`, and `link` are concrete
methods whose body raises `ProviderError`, so a partial adapter still constructs and
callers branch on exit code 3.

```python
# Correct — in adapters/base.py, an optional verb
def link(self, key: str, to: str, link_type: str) -> None:
    raise ProviderError(f"link not supported by provider '{self.config.provider}'")

# Wrong — @abstractmethod here would make every existing adapter fail to instantiate
```

**3. Read project settings through config, never hardcode them.** Repo names, branch
formats, reviewers, build/test/typecheck commands, and deploy workflows are all
`tkt cfg` lookups. In skill text this is absolute; in code it means going through
`Config.get()` rather than embedding a default.

```
# Correct (skill text)          # Wrong
tkt cfg build.test --pkg web    pnpm --filter web test
tkt cfg vcs.default_branch      main
```

**4. Idempotent writers compare hashes before touching disk.** `sync-pack` must leave
`git status` clean on a second run, so it hashes content and skips unchanged files
instead of rewriting them. Any new file-writing code inherits this expectation.

## Verify Checklist

Before presenting any code change:

- [ ] `python3 -m unittest tests.test_run tests.test_jira_adf` passes (58 tests).
- [ ] No new third-party import — `grep -rn "^import \|^from " core/ adapters/` shows
      stdlib and intra-project modules only.
- [ ] Nothing in `core/` names a provider, and no adapter is imported outside
      `core/registry.py`'s lazy import.
- [ ] Every new failure path raises a typed `TktError` subclass so the exit code is
      right; nothing prints an error and returns 0.
- [ ] Any new adapter return value is a `Ticket`/`Worklog`/`Check`, with unsupported
      fields left at their defaults rather than faked.
- [ ] New optional fields honour the omit / `""` / value tri-state on both the
      argparse side and the adapter side.
- [ ] If `skills/`, `agents/`, or a skill `description` changed, `scripts/smoke-sync-pack.sh`
      still passes — the managed `AGENTS.md` block is generated from those descriptions.
- [ ] If the change touches a backend, it was exercised live (`tkt doctor`, then the
      affected verb) — there is no CI and no integration test to catch it.
