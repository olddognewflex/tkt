---
name: architecture
description: How the major pieces of this project connect and flow. Load when working on system design, integrations, or understanding how components interact.
triggers:
  - "architecture"
  - "system design"
  - "how does X connect to Y"
  - "integration"
  - "flow"
edges:
  - target: context/stack.md
    condition: when specific technology details are needed
  - target: context/decisions.md
    condition: when understanding why the architecture is structured this way
  - target: context/adapter-contract.md
    condition: when writing or changing anything inside adapters/, or when you need the exact verb signatures and normalized shapes
  - target: context/skill-pack.md
    condition: when the work touches skills/, agents/, or how the pack is distributed into consumer repos
  - target: context/run-driver.md
    condition: when the work touches `tkt run`, phase state, or headless pipeline execution
  - target: context/conventions.md
    condition: when about to write code and you need the enforced patterns
grounds_to:
  - node: "function:31474bd239bedc25f515eaab726a4492"
    fingerprint: "mh:64:7b226d696e68617368223a5b383939303735392c393631393038342c33303336373738352c333534353038352c34363932353136342c31303031363733342c363633363132322c35383230333130342c31363439363332322c353438353530362c3339323234372c31313236353438362c31373431353239352c31383537323237312c343831303535372c393032393333302c31313638323639382c313837303437372c31383131343633352c313535343731302c31303932323637352c32363739393939392c313830393030342c393832393038372c373534363634362c34393338373230332c31373332303832392c31363631333034382c32303238373639332c313431343134372c323633383732302c38343033333430302c323931373734342c31313431373433332c353936373833322c31303930323739302c393738373332302c3834393737392c32303431373732342c353731383634332c32393637313335342c383131373337302c31333034363236372c333030303133322c353630353534392c393232333838372c31363631363933342c31383633393231382c373832383038302c31343538373734342c32383034353339322c31373936333735382c31383737353335302c31373531313233392c31383334323430392c31333639383532342c3931333939372c313837353033322c333335393038342c31303831393534332c323035363330362c32343834353935362c31363233373233322c313930363332335d2c226e65696768626f7273223a5b2266756e6374696f6e3a3062326233336638656136336231323435343465643466373132333634393561222c2266756e6374696f6e3a3132623534323065363436626136626135386630616335326134626331363339222c2266756e6374696f6e3a3133646264653061623438666432363233383935396633333966646561386566222c2266756e6374696f6e3a3335663663346566663466333639616566646135663935666631626665383932222c2266756e6374696f6e3a3432396337633237386636343539306365613865623365333033643034323232222c2266756e6374696f6e3a3532393637653261623765323335363165386263656636666431306162616264222c2266756e6374696f6e3a3565626230323462346432613863633964386332613662653839623733333037222c2266756e6374696f6e3a3638353864306230633035613364376665373734333036646363386662653731222c2266756e6374696f6e3a3833663337663536316261356366343330323038633433313039376235353235222c2266756e6374696f6e3a3836383534663334636332633462363834363438303730633539663338653539222c2266756e6374696f6e3a3938386335376138343565343332373437623633313566633237393834396438222c2266756e6374696f6e3a6134656236313031343634623933633463383833323031663734666163346661222c2266756e6374696f6e3a6236323633663231313434306234313934306435643464316635333066636362222c2266756e6374696f6e3a6239666263333935643263396233653362363038653032613531343331396132222c226d6574686f643a3037643536643433393632653934366336343365333131313839313938373431222c226d6574686f643a3362663639653638303537373365643239626531663431396362383237613961222c226d6574686f643a6132313264643136343439303839343730366265643164326538396362346436222c226d6574686f643a6332363665363464623731613835353566353932373539356364656363306636222c226d6574686f643a6662333131353733386139636135313762346438316363336438333264393239225d2c22746f6b656e436f756e74223a313630357d"
  - node: "function:0b2b33f8ea63b124544ed4f71236495a"
    fingerprint: "mh:64:7b226d696e68617368223a5b383939303735392c38373531313530332c35333136383839352c3130343439313630302c3132343331303137302c37363839323333362c3135313431343531372c39383638323037392c37353636373238362c3230343835383035342c31333034313534362c31313236353438362c37323039333639302c31383537323237312c38333638393339322c3137353331323830352c3131383537303232312c383339303930342c39313736373631372c3131323338383930382c31303932323637352c33353836343430342c313830393030342c3133343232353733372c33303935343831322c34393338373230332c3135373538393738342c34333232353939382c35383735303930382c34333636393037392c3132303432363534392c3134363731353938312c343431393134332c32333430313839382c35373838323837382c3131383139353636382c38373634353933372c31303836393338392c35363936323936392c34303931363338312c36353530383733302c3531303733363634322c39303031373634392c3231313730313534362c3132323535393932332c3132323333393634382c36383337343337332c3132333334313832322c3135303730303839302c3130313333343538382c33303336353434372c38353538393134392c3238343730393039372c3131333438303239372c32353330383530382c37383932363934362c35343139343937332c3233353730393531362c3133323539373539362c37333335383338332c31323330333835362c37373637393735362c32333032383630312c313930363332335d2c226e65696768626f7273223a5b2266756e6374696f6e3a3331343734626432333962656463323566353135656161623732366134343932222c226d6574686f643a3030666337373739323331633963626335313963323433643361653439643932222c226d6574686f643a3437383934396231636631636535356238646534353634623032393530626131222c226d6574686f643a3932666363323537346537303531386431313066356464316139633661373830222c226d6574686f643a6136383566666536623566633938333331333864313433316434393562313535222c226d6574686f643a6238366539356163616663383363333961303665336362313961303034336565222c226d6574686f643a6332363665363464623731613835353566353932373539356364656363306636222c226d6574686f643a6364363962363061333734383634316536303935373535313134323230326332222c226d6574686f643a6561313062626164303463643130626137333435326638383961376363336563222c226d6574686f643a6562663737663866383032313663653936656631623633303037393535326438225d2c22746f6b656e436f756e74223a37367d"
last_updated: 2026-08-04
---

# Architecture

## System Overview

One command, one process, no daemon. The flow through a verb:

```
tkt <verb> ...
  → build_parser() parses; --config/--json are accepted on EITHER side of the verb
  → main() dispatches
      • init, sync-pack  → handled BEFORE any config load (they may run before .sdlc/ exists)
      • Config.load()    → --config → $TKT_CONFIG → nearest .sdlc/config.toml walking up from cwd
      • lane, cfg KEY    → answered from config alone, no adapter is constructed
        (exception: `cfg priorities` is backend-aware and does build one)
      • everything else  → get_adapter(config) lazy-imports adapters/<provider>.py
  → the adapter verb talks to its backend (acli/REST, gh, GraphQL, or local files)
  → the adapter returns core/schema.py dataclasses: Ticket | Worklog | Check
  → main() prints human text, or to_dict() as JSON when --json
  → any TktError subclass → message on stderr, process exits with its exit_code
```

The load-bearing split is that **process logic** (what step comes next) lives in
`skills/`, while **provider calls** (how to talk to a backend) live in `adapters/`.
They are connected only by the verb contract and the normalized schema, which is
why one skill drives an 8-lane Jira board and a 3-column markdown board unchanged.

Dispatch lives in [`main()`](mex://function:31474bd239bedc25f515eaab726a4492); provider
resolution lives in [`get_adapter()`](mex://function:0b2b33f8ea63b124544ed4f71236495a).

## Key Components

- **`core/cli.py`** — argparse parsing, verb dispatch, and output formatting. Also
  where input validation lives (dates, `--agent-status`) so a typo fails before it
  reaches a backend. Depends on `config`, `registry`, and the schema's `to_dict()`.
- **`core/config.py`** — loads `.sdlc/config.toml` and owns every mapping skills
  depend on: role↔lane, `full_sdlc`/`deliverable` issue-type routing, named queries,
  priority ordering, `[board.ownership]`, and dotted-path `get()`.
- **`core/registry.py`** — `provider name → (module, class)`, imported lazily so one
  adapter's missing optional dependency cannot break the other four.
- **`core/schema.py`** — `Ticket` / `Worklog` / `Check` dataclasses plus `to_dict()`.
  This is the JSON shape skills parse; adapters that cannot store a field leave it
  at its default rather than faking it.
- **`adapters/base.py`** — the ABC every adapter subclasses. Nine `@abstractmethod`
  verbs are mandatory; `create`/`apply`/`edit`/`link` are optional and default to a
  "not supported" `ProviderError`. See `context/adapter-contract.md`.
- **`core/query.py`** — a tiny shared JQL-subset evaluator (`=`, `!=`, `IS EMPTY`,
  `AND`, `ORDER BY`, `currentUser()`), used by the adapters with no native query
  language: markdown, linear, openkanban.
- **`core/pack.py`** — `tkt sync-pack`: installs the skill pack into a consumer repo
  as committed copies, tracked in `.sdlc/pack-manifest.json`, plus a marker-delimited
  managed block in the consumer's `AGENTS.md`. See `context/skill-pack.md`.
- **`core/run.py`** — `tkt run`, the external loop driver that executes one pipeline
  phase per harness invocation. Self-contained subsystem; see `context/run-driver.md`.
- **`core/scaffold.py` + `core/toolchain.py`** — `tkt init`. Copies the matching
  `examples/config.<provider>.toml` and rewrites its `[build]` table from whatever the
  target project already declares.
- **`core/ticketdoc.py`** — the canonical full-ticket markdown document (frontmatter +
  body) that `tkt apply` ingests and the `$EDITOR` flow round-trips. Backend-agnostic;
  adapters map the parsed result onto their own storage.

## External Dependencies

Every one of these is reached by shelling out or by a stdlib HTTP call — there is no
client library in the tree.

- **`acli` + Jira REST + Tempo** — the `jira` adapter. `acli` for most reads/writes,
  raw REST where `acli` has no equivalent, Tempo for worklogs.
- **`gh` CLI** — the `github` adapter, for Issues and either Projects v2 board status
  or `Status:` labels. Projects v2 needs `gh auth login -s project,read:project`.
- **`api.linear.app/graphql`** — the `linear` adapter, authenticated with `LINEAR_API_KEY`.
- **Local filesystem** — the `markdown` adapter (one `<KEY>.md` per ticket plus JSONL
  state sidecars) and `openkanban` (a single local JSON store). No auth, no network.
- **`git`** — only inside `core/pack.py`, to stamp `pack_commit` into the consumer
  manifest and to tell `tkt doctor` whether the pack checkout has moved since the last sync.

`tkt doctor` is the single place that reports whether a backend's CLI is missing,
auth is bad, or the configured lanes do not exist on the real board.

## What Does NOT Exist Here

- **No third-party Python dependencies, no packaging, no build step.** There is no
  `pyproject.toml`, `setup.py`, or `Makefile`. Installation is "clone and symlink".
- **No CI.** `.github/` holds only Copilot prompt translations — there are no workflows.
  Nothing runs the tests except a human at a terminal.
- **No VCS operations inside `tkt`.** Branching, pushing, PRs, and merges are the
  skills' job (they call `gh` directly); `tkt` only ever moves ticket state. `core/run.py`
  holds the same line deliberately.
- **No server, daemon, database, or cache.** Every verb is a cold start that reads
  config, hits the backend once, prints, and exits.
- **No backend specifics above `adapters/`.** Nothing in `core/` or `skills/` may
  mention Jira, `gh`, or GraphQL.
