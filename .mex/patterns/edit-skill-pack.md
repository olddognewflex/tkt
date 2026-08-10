---
name: edit-skill-pack
description: Change a skill or agent in the pack — the portability rules, which harness translations to regenerate, and why a frontmatter description edit reaches every consumer repo.
triggers:
  - "edit a skill"
  - "SKILL.md"
  - "new skill"
  - "sync-skills"
  - "translation"
  - "agents/"
  - "prompt file"
edges:
  - target: context/skill-pack.md
    condition: always — it has the portability rules, the trust scoping, and the full tier table
  - target: patterns/debug-run-loop.md
    condition: when the skill you changed is a pipeline phase and `tkt run` now behaves differently
  - target: context/conventions.md
    condition: when the skill change is paired with a code change in core/ or adapters/
grounds_to:
  - node: "function:c56b69cb958e6bca40e6b32febe1abee"
    fingerprint: "mh:64:7b226d696e68617368223a5b31343733343031332c38373531313530332c33353932303930332c333534353038352c313334303435302c393635343732332c37313138313138322c333634333031332c3836303337332c31313130393635372c31373931303839362c31313236353438362c34323234323235342c3131323833333434382c32383331363038322c32373732313037342c34383334323731312c383339303930342c38343533323839342c323134373331302c37333933373831302c3137363130323036302c32363136383537312c3130303731353330342c37333232373136312c38353335393234352c35363034373432302c36323732363830362c3138373233373630332c313634363838322c33313736313835342c32353032353638372c343431393134332c33373037373835342c38383736343531392c39323939313337332c3131303731353836362c34383736313733332c35363936323936392c3532363836353634392c32313136363535352c32333036333234392c39303031373634392c32323637323731362c34393132303034322c35363731383333372c36383337343337332c31363631363131312c3130333037363331372c36323530353438362c33303336353434372c3134353331333732372c35393035303231312c33313933363034332c323834343539392c37383932363934362c3931333939372c31333030323132332c38393133323030382c37333335383338332c31363938363130362c37373637393735362c37373130313436362c31313732363932355d2c226e65696768626f7273223a5b2266756e6374696f6e3a6433366466643235633330633164663235363366373864303336373233316132225d2c22746f6b656e436f756e74223a3133347d"
  - node: "function:d36dfd25c30c1df2563f78d0367231a2"
    fingerprint: "mh:64:7b226d696e68617368223a5b32313931393135382c31393930323330372c33303336373738352c33343939333831332c3130323331383931302c33303133353032342c363633363132322c38373735373730362c37353636373238362c35313039363337352c31373931303839362c31313236353438362c3338343633392c31333233373537382c343831303535372c32373732313037342c393238363734332c373535353739342c3130363832303631332c33363136303539332c32323437393732392c37333436353130332c3134343833343534302c393832393038372c36303931383634302c33323632313934382c39363937353634322c31363631333034382c34383230323433322c32313435343331332c31353735333834312c3135313936343834302c343431393134332c32333430313839382c38383736343531392c3132353633373736312c3133333134353639322c35343235363131372c34303231353731352c38373037373636332c35373735323230352c3132363231303135342c39303031373634392c37343832333037332c3130313336313335382c3132393138363339382c35383039323536372c34313735363038362c31373132363631332c3132303632313938342c33303336353434372c38313333393138352c36303833393434332c373336353636312c3133333933363230342c34353837343630372c35343139343937332c33343130313934382c33373332393138322c34383832383739352c3232333035393732372c32393939383334352c39313837303739332c31313732363932355d2c226e65696768626f7273223a5b2266756e6374696f6e3a3638353864306230633035613364376665373734333036646363386662653731222c2266756e6374696f6e3a6335366236396362393538653662636134306536623332666562653161626565222c2266756e6374696f6e3a6661316464333566356265323565323633316565346261373830656330623963225d2c22746f6b656e436f756e74223a3131307d"
last_updated: 2026-08-10
---

# Edit the Skill Pack

## Context

`skills/<name>/SKILL.md` is the canonical version. Everything in `.github/prompts/`,
`.cursor/`, `.kiro/`, `.gemini/`, `.agents/`, `.windsurf/`, `.clinerules/`,
`.continue/`, `.augment/`, and `.opencode/` is a **translation** of it. Editing a
translation directly guarantees drift, because the next `sync-skills` pass regenerates
from the canonical file.

Load `context/skill-pack.md` for the full tier table and the trust scoping of `agents/`.

## Steps

1. **Edit `skills/<name>/SKILL.md`.** Nothing else, first.

2. **Re-check the portability rules against your diff:**
   - Semantic verbs only — no `acli`, `gh issue`, Jira REST, or Linear GraphQL for
     ticketing. (`gh` for PRs and CI is fine; that is VCS, not ticketing.)
   - Roles, not lane names: `tkt transition KEY review`, never `"In Review"`.
   - Every toolchain, repo, branch, reviewer, and workflow value comes from `tkt cfg`.
   - Branch on exit code 3 rather than assuming an optional verb exists.
   - Time-tracking output must read naturally when tracking is off.
   - Genuinely infra-specific steps get marked `PROJECT-SPECIFIC`.

3. **Decide the translation scope**, per the `sync-skills` tiers:
   - **Tier A — every skill:** `.agents/skills/`, `.kiro/skills/`,
     `.github/prompts/<name>.prompt.md`, `.gemini/commands/<name>.toml`.
   - **Tier B — only the seven core pipeline skills** (`select-ticket`,
     `triage-ticket`, `plan-ticket`, `open-pr`, `self-review`, `respond-to-review`,
     `deploy-ready`): `.cursor/skills/`, `.cursor/commands/`, `.windsurf/workflows/`,
     `.clinerules/workflows/`, `.continue/prompts/`, `.augment/commands/`,
     `.agents/workflows/`, `.opencode/commands/`.
   - **Tier C — do NOT touch for a skill change:** root `AGENTS.md`, `.cursor/rules/`,
     `.kiro/steering/`, and the other rules-style surfaces. Those change only when
     project-wide conventions change.

4. **Regenerate the translations** by invoking the `sync-skills` skill, which owns the
   per-format details (Copilot prompt frontmatter, Gemini TOML, OpenCode `$ARGUMENTS`
   and `!`-backtick shell injection, Antigravity `// turbo` markers).

5. **If you changed the frontmatter `description`, stop and reread it.** The **first
   sentence** is extracted by
   [`_skill_oneliner()`](mex://function:c56b69cb958e6bca40e6b32febe1abee) and rendered
   into the managed `AGENTS.md` block by
   [`_agents_block()`](mex://function:d36dfd25c30c1df2563f78d0367231a2) in **every
   consumer repo** on their next `tkt sync-pack`. It is public-facing copy.

6. **Adding a wholly new skill?** It ships automatically — the shipped set is derived
   by scanning `skills/*/SKILL.md`, not from a list. Update the skill table in
   `AGENTS.md`, and add it to Tier B translations only if it is a core pipeline phase.
   If it is a pipeline phase executed by `tkt run`, check whether `PHASES` and
   `PHASE_NAMES` in `core/run.py` need it too.

7. **Run the smoke test:** `bash scripts/smoke-sync-pack.sh`.

## Gotchas

- **Editing a translation instead of the canonical skill** is the single most common
  mistake here. It survives review and dies at the next sync.
- **A `description` edit is a distributed change.** It rewrites the managed block in
  every consumer's `AGENTS.md`, which shows up as a diff in repos you are not looking at.
- **The managed block must be byte-stable across runs** or `sync-pack` idempotency
  breaks and `git status` stops being clean after a no-op sync. Anything that makes the
  block's content depend on time, cwd, or dict ordering is a bug.
- **`sync-skills` is pack-internal and never ships** — it is excluded from the shipped
  skill set. Do not translate it.
- **This repo's own `AGENTS.md` has no managed block** and is hand-maintained. Do not
  add markers to it.
- **`agents/` is trust-scoped on purpose.** `sdlc-executor` cannot push or transition;
  reviewers and planners are read-only. Widening a subagent's tools lets the pipeline
  self-approve, which the design specifically prevents. Every delegating phase must
  also keep its inline fallback so harnesses without subagents still work.
- **New harness directory?** It must be registered in `_HARNESSES` in `core/pack.py`,
  or `sync-pack` will never copy it no matter how many files you add.

## Verify

- [ ] `bash scripts/smoke-sync-pack.sh` — every case PASS.
- [ ] `grep -rniE "acli|gh issue|jira|linear|/graphql" skills/<name>/SKILL.md` finds
      nothing (occurrences in VCS/`gh` PR steps are the only acceptable hits).
- [ ] No hardcoded repo name, branch name, package manager, or build command — each is
      a `tkt cfg` read.
- [ ] Tier A translations regenerated for this skill; Tier B only if it is one of the
      seven core skills; Tier C untouched.
- [ ] `./tkt sync-pack --check --dir <a consumer repo>` behaves as expected — reports
      out-of-date before syncing, clean after.
- [ ] A second `./tkt sync-pack --dir <consumer>` leaves that repo's `git status` clean.
- [ ] The skill table in `AGENTS.md` matches `ls skills/`.

## Debug

- Consumer does not see the change → they have not re-run `tkt sync-pack`;
  `tkt doctor` there reports `pack_commit` differing from the pack's HEAD.
- Second sync produces a diff → the managed block is not deterministic, or a shipped
  file's content depends on the environment.
- A harness's files never appear in the consumer → that harness is not in the
  manifest. Selection is additive and sticky, so add it once with
  `tkt sync-pack <harness> --dir <consumer>`; `--list-harnesses` shows what is installed.
- `sync-pack` warns "locally-modified" → the consumer edited a pack copy. That is
  reported, never silently overwritten, and never deleted.

## Update Scaffold
- [ ] Update `.mex/ROUTER.md` "Current Project State" if what's working/not built has changed
- [ ] Update any `.mex/context/` files that are now out of date
- [ ] If this is a new task type without a pattern, create one in `.mex/patterns/` and add to `INDEX.md`
