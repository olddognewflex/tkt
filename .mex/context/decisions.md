---
name: decisions
description: Key architectural and technical decisions with reasoning. Load when making design choices or understanding why something is built a certain way.
triggers:
  - "why do we"
  - "why is it"
  - "decision"
  - "alternative"
  - "we chose"
edges:
  - target: context/architecture.md
    condition: when a decision relates to system structure
  - target: context/stack.md
    condition: when a decision relates to technology choice
  - target: context/adapter-contract.md
    condition: when the decision is about what an adapter must or may implement
  - target: context/skill-pack.md
    condition: when the decision is about how the pack reaches consumer repos
grounds_to:
  - node: "function:0b2b33f8ea63b124544ed4f71236495a"
    fingerprint: "mh:64:7b226d696e68617368223a5b383939303735392c38373531313530332c35333136383839352c3130343439313630302c3132343331303137302c37363839323333362c3135313431343531372c39383638323037392c37353636373238362c3230343835383035342c31333034313534362c31313236353438362c37323039333639302c31383537323237312c38333638393339322c3137353331323830352c3131383537303232312c383339303930342c39313736373631372c3131323338383930382c31303932323637352c33353836343430342c313830393030342c3133343232353733372c33303935343831322c34393338373230332c3135373538393738342c34333232353939382c35383735303930382c34333636393037392c3132303432363534392c3134363731353938312c343431393134332c32333430313839382c35373838323837382c3131383139353636382c38373634353933372c31303836393338392c35363936323936392c34303931363338312c36353530383733302c3531303733363634322c39303031373634392c3231313730313534362c3132323535393932332c3132323333393634382c36383337343337332c3132333334313832322c3135303730303839302c3130313333343538382c33303336353434372c38353538393134392c3238343730393039372c3131333438303239372c32353330383530382c37383932363934362c35343139343937332c3233353730393531362c3133323539373539362c37333335383338332c31323330333835362c37373637393735362c32333032383630312c313930363332335d2c226e65696768626f7273223a5b2266756e6374696f6e3a3331343734626432333962656463323566353135656161623732366134343932222c226d6574686f643a3030666337373739323331633963626335313963323433643361653439643932222c226d6574686f643a3437383934396231636631636535356238646534353634623032393530626131222c226d6574686f643a3932666363323537346537303531386431313066356464316139633661373830222c226d6574686f643a6136383566666536623566633938333331333864313433316434393562313535222c226d6574686f643a6238366539356163616663383363333961303665336362313961303034336565222c226d6574686f643a6332363665363464623731613835353566353932373539356364656363306636222c226d6574686f643a6364363962363061333734383634316536303935373535313134323230326332222c226d6574686f643a6561313062626164303463643130626137333435326638383961376363336563222c226d6574686f643a6562663737663866383032313663653936656631623633303037393535326438225d2c22746f6b656e436f756e74223a37367d"
  - node: "function:52967e2ab7e23561e8bcef6fd10ababd"
    fingerprint: "mh:64:7b226d696e68617368223a5b31373636313031392c31393930323330372c3135383130313934322c35313335313737382c3132343331303137302c36393635353932352c3132363433303439332c34383430333936312c3131373839373033322c35313039363337352c31373931303839362c31313236353438362c34323234323235342c35323931303230312c3133343539313431312c32333131313836382c34383334323731312c33313536393930312c35303631363336382c38323939393730362c35383138323739332c3132363330323535342c37363634363333362c31373032393134382c39373435303534342c38353335393234352c3132363036323234332c31363631333034382c35383735303930382c313634363838322c323633383732302c3134363731353938312c343431393134332c36353436393030362c39383937383731342c3137333636363438332c35313032393036322c31303836393338392c383439393835342c3130383230343831332c32393637313335342c3132363231303135342c31333034363236372c3130313136333735342c39383335393132352c393232333838372c38343439313939362c3131363932323034302c373832383038302c35333830353139352c32383034353339322c37333535363435332c31383737353335302c37353735393938392c3133323139363139382c36343333343932352c33393934363934322c32313230303531332c34373735353230342c34383638383035342c31373433353234322c32343834353935362c323935333732322c31313732363932355d2c226e65696768626f7273223a5b2266756e6374696f6e3a3331343734626432333962656463323566353135656161623732366134343932222c2266756e6374696f6e3a6430643139643631393339663664386334363734393437636562376465353939225d2c22746f6b656e436f756e74223a313334327d"
  - node: "function:6858d0b0c05a3d7fe774306dcc8fbe71"
    fingerprint: "mh:64:7b226d696e68617368223a5b383939303735392c393330323832352c373839313932342c333534353038352c32383432333736312c31393030333336302c363633363132322c31333530393139342c3134323938322c31313130393635372c31333034313534362c31313236353438362c31303336313937372c31383537323237312c343831303535372c31323832373832352c323934373739342c373535353739342c33373437353736332c333237303332372c33383137363739372c383339363836322c313830393030342c393832393038372c373534363634362c3836323935322c31313332313131352c31303537373234392c32303732303730312c313431343134372c323633383732302c33343130383239302c343431393134332c323734383236392c31393737313632362c32333234313035322c393738373332302c31333730383630332c32303431373732342c353731383634332c333739373038362c383131373337302c3832363536312c31313232343236322c353630353534392c393232333838372c333731323733372c34363739363633322c373832383038302c383039313432332c32383034353339322c31343733313133312c31383737353335302c32343637313637362c31363730373933312c31393438363037312c33393934363934322c313830313638392c383538363932332c31303831393534332c31323330333835362c32343834353935362c323935333732322c31313732363932355d2c226e65696768626f7273223a5b2266756e6374696f6e3a3038306539636465303838643834393565626539363039323865663665363434222c2266756e6374696f6e3a3238633339616537616633373134336131366361396662376461663633653939222c2266756e6374696f6e3a3265613063346539633835376139623339303333346464363363653238643636222c2266756e6374696f6e3a3331343734626432333962656463323566353135656161623732366134343932222c2266756e6374696f6e3a3434636265396435623739373965666161376330633265643434323033366563222c2266756e6374696f6e3a3738653033666431393637316633303235376535633839373630663434373937222c2266756e6374696f6e3a3962376236346466333662386233363631316235323731363935323339373738222c2266756e6374696f6e3a6139653134633332343663363835303931356331386262363431366365393831222c2266756e6374696f6e3a6433366466643235633330633164663235363366373864303336373233316132222c2266756e6374696f6e3a6465333234376639313338356231393333393332373330326638343734316566222c2266756e6374696f6e3a6532623364353664303565303938373363313637303963386664633962333939222c2266756e6374696f6e3a6562626462353531316237643666663436653338343465353363313438353536222c226d6574686f643a6332363665363464623731613835353566353932373539356364656363306636225d2c22746f6b656e436f756e74223a3935397d"
  - node: "function:5a5b4f21946edff1749db8729890cdb0"
    fingerprint: "mh:64:7b226d696e68617368223a5b31383134343139392c39393137343532392c3137383335323231362c31373734373833372c3136343632373732302c33303133353032342c34313735343830312c3130333434373737352c31363439363332322c35313039363337352c31373931303839362c37313039333733372c383534383930382c35323931303230312c373633323832392c32373732313037342c32393732353036382c383339303930342c3135323534393132352c32323036333131372c35333337353439302c3132363330323535342c313830393030342c33363535323038372c3239353131343235312c35363137333037352c31373332303832392c31303537373234392c39303431353236382c33343435303338382c33313736313835342c3134363731353938312c343431393134332c32333830313035332c38383736343531392c37353738333033342c3131303731353836362c31303836393338392c35363534363237342c33323738313937302c32313831343130372c37363735343934312c3832363536312c36363539393130392c343838303534322c3132393138363339382c35383039323536372c39343232353939312c3132323930393536302c32323934333537332c33303336353434372c35393638393230342c3236313136363237392c35383037343433332c31363730373933312c37383932363934362c35373231353235322c313830313638392c383538363932332c3135373630303233342c323035363330362c32393939383334352c32333032383630312c31313732363932355d2c226e65696768626f7273223a5b226d6574686f643a3030666337373739323331633963626335313963323433643361653439643932222c226d6574686f643a3536366363653765366164653663313366363763336539663964393766646431222c226d6574686f643a3730396533643232336531333036373235383030306337366362313763333939222c226d6574686f643a3936353633663138663164633130333365643461313464353237616564343861222c226d6574686f643a6136383566666536623566633938333331333864313433316434393562313535222c226d6574686f643a6238366539356163616663383363333961303665336362313961303034336565222c226d6574686f643a6364363962363061333734383634316536303935373535313134323230326332222c226d6574686f643a6562663737663866383032313663653936656631623633303037393535326438225d2c22746f6b656e436f756e74223a3135347d"
last_updated: 2026-08-10
---

# Decisions

## Decision Log

### Split process logic from provider calls behind semantic verbs
**Date:** 2026-06-02
**Status:** Active
**Decision:** Skills call semantic verbs (`tkt view`, `tkt transition`, ...) and every
backend is normalized into one JSON ticket shape; no skill ever touches `acli`, `gh`,
or a REST/GraphQL endpoint.
**Reasoning:** The SDLC pipeline is the valuable, slow-to-write part. Coupling it to
Jira would mean rewriting all twenty skills to support a second backend.
**Alternatives considered:** A thin Jira wrapper with escape hatches (rejected — the
escape hatches become the interface); per-backend skill variants (rejected — twenty
skills times five backends is unmaintainable).
**Consequences:** Adding a provider requires zero skill changes, which is the project's
core promise. The cost is that anything a backend cannot express must either be
absent from the schema or degrade to a documented default, and new capabilities can
only enter through the verb contract.

### Speak in roles, map to lanes in config
**Date:** 2026-06-02
**Status:** Active
**Decision:** Skills use canonical roles (`in_progress`, `review`, `qa_ready`, ...);
`[board.roles]` in `.sdlc/config.toml` maps each role to the provider's literal lane
string.
**Reasoning:** Board vocabulary is per-team, not per-backend. One Jira project calls it
"In Review", another "Code Review", a markdown board just has three columns.
**Alternatives considered:** Standardizing lane names across backends (rejected —
tkt does not own other teams' boards); inferring lanes by fuzzy match (rejected —
silently transitioning to the wrong lane is worse than failing).
**Consequences:** An unmapped role is a hard `NotFoundError`, not a guess. A board with
fewer lanes simply maps several roles to the same lane, and the pipeline still runs.

### Lazy-import adapters in the registry
**Date:** 2026-06-02
**Status:** Active
**Decision:** `core/registry.py` stores `provider name → (module, class)` strings and
calls `importlib.import_module` inside
[`get_adapter()`](mex://function:0b2b33f8ea63b124544ed4f71236495a).
**Reasoning:** A top-level import of all five adapters means any one of them failing to
import — a syntax error, a missing optional dependency — breaks every provider,
including `tkt doctor`, which is the tool you reach for when something is broken.
**Alternatives considered:** Eager imports with try/except around each (rejected —
hides real errors and still pays the import cost); a plugin entry-point system
(rejected — needs packaging, which this project does not have).
**Consequences:** Import errors surface only when that provider is actually selected,
which is what you want. `core/` stays free of adapter imports, and that invariant is
worth preserving in any refactor.

### Optional verbs default to a ProviderError instead of being abstract
**Date:** 2026-06-02 (extended 2026-06-15 when `edit` was added)
**Status:** Active
**Decision:** `whoami`, `list`, `view`, `transition`, `comment`, `blockers`, `worklog`,
`lane_time`, and `doctor` are `@abstractmethod`. `create`, `apply`, `edit`, and `link`
are concrete methods on `Adapter` whose bodies raise `ProviderError` (exit code 3).
**Reasoning:** Not every backend can create or link tickets, but every backend can be
read and moved. Making the optional four abstract would force five adapters to write
stub overrides, and would break instantiation the moment a sixth verb was added.
**Alternatives considered:** A `supports()` capability method (rejected — callers would
still have to handle the failure path, so it adds a second thing to keep in sync);
returning `None` (rejected — silent no-ops in a pipeline are the worst outcome).
**Consequences:** Callers branch on exit code 3 rather than probing for support. Adding
a new optional verb is backward-compatible by construction: give it a body on the base
class that raises, and existing adapters keep working.

### Accept `--config` and `--json` on either side of the verb, via argparse SUPPRESS
**Date:** 2026-06-15
**Status:** Active
**Decision:** The shared flags live on both the top-level parser and every subparser
(through `parents=`), with `default=argparse.SUPPRESS`; real defaults are applied
post-parse in `main()`.
**Reasoning:** `tkt --config X view K` and `tkt view K --config X` must behave
identically. With ordinary defaults the subparser's copy re-applies its default and
silently clobbers a value the top-level parser already set — `--config` would be lost
with no error at all.
**Alternatives considered:** Top-level-only flags (rejected — the ergonomics the pack's
skills rely on); manual `sys.argv` pre-scan (rejected — reimplements argparse badly).
**Consequences:** [`build_parser()`](mex://function:52967e2ab7e23561e8bcef6fd10ababd)
must never seed these two flags with `set_defaults()` — `parents=` copies actions by
reference, so that would mutate the shared action and revive the clobber. Any future
"works on both sides" flag has to follow the same construction.

### `sync-pack` installs committed copies, not symlinks
**Date:** 2026-07-08 (named/sticky harness selection added 2026-07-28)
**Status:** Active
**Decision:** [`sync_pack()`](mex://function:6858d0b0c05a3d7fe774306dcc8fbe71) copies
pack files into the consumer tree, records every path in `.sdlc/pack-manifest.json`,
and maintains a marker-delimited managed block in the consumer's `AGENTS.md`.
**Reasoning:** Cloud harnesses (GitHub Copilot, Kiro) and CI only see *tracked files*.
The symlinks that `tkt init --link-skills` writes point at a local clone and are
invisible to them, so the pack silently did not exist in exactly the environments that
most needed it.
**Alternatives considered:** Keeping symlinks only (rejected — breaks cloud harnesses);
a git submodule (rejected — consumers must be able to edit and commit their copy, and
submodules are a support burden); publishing to a package registry (rejected — the
project has no packaging).
**Consequences:** Consumers carry a copy that can drift, so `sync-pack --check` and
`tkt doctor` both report staleness. Idempotency is a hard requirement — a second run
with an unchanged pack must leave `git status` clean — which is why the writer hashes
before writing and never deletes. Harness selection is additive and sticky: once a
harness is recorded in the manifest, later bare runs keep it in sync.

### `tkt run` drives one phase per harness invocation and never touches VCS
**Date:** 2026-07-27
**Status:** Active
**Decision:** The loop driver in `core/run.py` invokes the configured harness once per
pipeline phase, reads a `result.json` contract back, and loops until a gate, a STOP
file, or a cap. It performs no git or PR operations and bypasses no adapter verb.
**Reasoning:** A single long-running agent session drifts and cannot be resumed. One
invocation per phase gives a natural checkpoint, a bounded blast radius per failure,
and a retry unit. Keeping VCS out of the driver means the driver can never do something
irreversible that the skills did not ask for.
**Alternatives considered:** One long agent session per ticket (rejected — no resume
point, unbounded context); a hosted job runner (rejected — the project has no server
and no CI).
**Consequences:** Phase state must be durable outside the process, which forces the
marker design below. Human gates are enforced by the driver rather than by trusting the
agent: P9 always stops, P10 stops unless `[board.ownership]` explicitly grants
`deploy_ready->done` to an agent, and any human-owned transition halts the run.

### Persist run state as ticket comments, mirrored locally
**Date:** 2026-07-27
**Status:** Active
**Decision:** Each phase writes a `<!-- tkt-run: {...} -->` marker as a ticket comment,
and also mirrors it to `.sdlc/state/run/<key>/marker.json`.
**Reasoning:** The ticket comment is the authoritative record — it survives a lost
worktree and is readable from another machine. But the verb contract has no "list
comments" verb, and only the markdown adapter can hand raw comment bodies back, so on
Jira/GitHub/Linear there is no way to read the marker back.
**Alternatives considered:** Adding a `comments` verb to the contract (rejected — a new
mandatory verb for every adapter, to serve one subsystem); local state only (rejected —
loses the cross-machine property that motivated comments).
**Consequences:** [`read_ticket_marker()`](mex://function:5a5b4f21946edff1749db8729890cdb0)
tries the adapter's raw read first and falls back to the local mirror. Without that
fallback a resume on Jira/GitHub/Linear silently restarts at P1 and redoes work. A
failed mirror write is swallowed deliberately: the comment already landed, and losing
resume fidelity on one machine must not abort a run.

### Test offline with stdlib `unittest`; validate backends by hand
**Date:** 2026-07-27
**Status:** Active
**Decision:** `tests/test_run.py` and `tests/test_jira_adf.py` (58 tests) run fully
offline with `python3 -m unittest`, using the markdown adapter against temp dirs and a
stub harness script. Backend behaviour is validated by running real verbs against a
real board, plus `scripts/smoke-sync-pack.sh`.
**Reasoning:** The no-dependencies rule rules out pytest and mocking libraries, and
there is no CI to run anything anyway. The parts worth unit-testing are the pure ones:
the loop driver's state machine and the Jira Markdown→ADF converter.
**Alternatives considered:** Recorded HTTP fixtures (rejected — needs a library, and
fixtures rot against live boards); no tests at all (rejected once the run driver's
state machine became too intricate to verify by inspection).
**Consequences:** Adapter code paths are effectively untested — a change to
`adapters/*.py` must be exercised live before it is trusted. Nothing runs the suite
automatically; running it is part of the verify checklist, not a pipeline's job.
