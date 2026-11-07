// Self-test for check-artifact-format.mjs.
// Run: node --test enforcement/*.test.mjs
//
// Drives the script as a subprocess (the way tkt will call it) so the CLI
// contract is what is tested: stderr finding lines, --json, exit codes.
// Each rule is exercised once with input it must allow and once with input it
// must flag, against a throwaway rules file that enables only that rule.

import { strict as assert } from "node:assert";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, describe, test } from "node:test";
import { fileURLToPath } from "node:url";

const SCRIPT = fileURLToPath(new URL("./check-artifact-format.mjs", import.meta.url));

let dir;
let counter = 0;
before(() => {
  dir = mkdtempSync(join(tmpdir(), "tkt-artifact-format-"));
});
after(() => {
  rmSync(dir, { recursive: true, force: true });
});

function tmpFile(name, content) {
  const path = join(dir, `${counter++}-${name}`);
  writeFileSync(path, content);
  return path;
}

function run(args, input = "") {
  const r = spawnSync(process.execPath, [SCRIPT, ...args], { input, encoding: "utf8" });
  return {
    code: r.status,
    out: r.stdout,
    err: r.stderr,
    lines: r.stderr.split("\n").filter(Boolean),
  };
}

// Rules file with a single preset "t" holding `kindRules` for `kind`.
function rulesFile(kindRules, kind = "ticket") {
  return tmpFile("rules.json", JSON.stringify({ version: 1, presets: { t: { [kind]: kindRules } } }));
}

// Findings (as parsed from --json) for `text` under `kindRules`.
function findings(kindRules, text, kind = "ticket") {
  const r = run(["--kind", kind, "--preset", "t", "--rules", rulesFile(kindRules, kind), "--json"], text);
  assert.equal(r.code, 0, r.err);
  return JSON.parse(r.out).findings;
}
const ruleIds = (fs) => fs.map((f) => f.rule);

// Presets from the shipped rules file.
function preset(name, kind, text, extra = []) {
  const r = run(["--kind", kind, "--preset", name, "--json", ...extra], text);
  assert.equal(r.code, 0, r.err);
  return JSON.parse(r.out).findings;
}

const md = (...lines) => lines.join("\n") + "\n";

describe("title", () => {
  const rules = { require_title: true };
  test("allows exactly one title", () => {
    assert.deepEqual(findings(rules, md("# A title", "", "Body text.")), []);
  });
  test("flags a missing title", () => {
    assert.deepEqual(ruleIds(findings(rules, md("Body text.", "## Summary", "x"))), ["title"]);
  });
  test("flags two titles, pointing at the second", () => {
    const fs = findings(rules, md("# One", "text", "# Two", "text"));
    assert.deepEqual(ruleIds(fs), ["title"]);
    assert.equal(fs[0].line, 3);
  });
  test("is off when require_title is false", () => {
    assert.deepEqual(findings({ require_title: false }, md("no title")), []);
  });
});

describe("thesis", () => {
  const rules = { require_thesis: true };
  test("allows prose between the title and the first section", () => {
    assert.deepEqual(findings(rules, md("# T", "", "The thesis.", "", "## Summary", "x")), []);
  });
  test("allows prose at the start when there is no title", () => {
    assert.deepEqual(findings(rules, md("The thesis.", "## Summary", "x")), []);
  });
  test("flags a title followed directly by a section", () => {
    assert.deepEqual(ruleIds(findings(rules, md("# T", "", "## Summary", "x"))), ["thesis"]);
  });
  test("does not take a code fence, an HTML comment or a rule as a thesis", () => {
    const text = md("# T", "<!-- thesis goes here -->", "```", "code words", "```", "---", "## Summary", "x");
    assert.deepEqual(ruleIds(findings(rules, text)), ["thesis"]);
  });
  test("does not take a ### subheading as a thesis", () => {
    assert.deepEqual(ruleIds(findings(rules, md("# T", "### Aside", "## Summary", "x"))), ["thesis"]);
  });
});

describe("missing-section", () => {
  const rules = { required: ["Summary", ["Behavior", "Business Process"]] };
  test("allows all required sections, either alternative", () => {
    assert.deepEqual(findings(rules, md("## Summary", "x", "## Business Process", "y")), []);
  });
  test("flags each missing entry and names the alternatives", () => {
    const fs = findings(rules, md("## Other", "x"));
    assert.deepEqual(ruleIds(fs), ["missing-section", "missing-section"]);
    assert.equal(fs[0].message, 'required section "## Summary" not found');
    assert.equal(fs[1].message, 'required section "## Behavior" or "## Business Process" not found');
  });
});

describe("section-order", () => {
  const rules = { required: ["Problem", "Proposal", "Acceptance"] };
  test("allows the listed order, with other sections between", () => {
    const text = md("## Problem", "a", "## Notes", "n", "## Proposal", "b", "## Acceptance", "c");
    assert.deepEqual(findings(rules, text), []);
  });
  test("flags a required section that comes too early", () => {
    const fs = findings(rules, md("## Proposal", "b", "## Problem", "a", "## Acceptance", "c"));
    assert.deepEqual(ruleIds(fs), ["section-order"]);
    assert.equal(fs[0].message, 'section "## Proposal" must come after "## Problem"');
    assert.equal(fs[0].line, 1);
  });
  test("can be disabled while keeping missing-section", () => {
    const r = { ...rules, disable: ["section-order"] };
    const text = md("## Proposal", "b", "## Problem", "a");
    assert.deepEqual(ruleIds(findings(r, text)), ["missing-section"]);
  });
});

describe("first-section", () => {
  const rules = { first_section: "Summary" };
  test("allows the section first", () => {
    assert.deepEqual(findings(rules, md("## Summary", "x", "## Other", "y")), []);
  });
  test("allows its absence", () => {
    assert.deepEqual(findings(rules, md("## Other", "y")), []);
  });
  test("flags it in a later position", () => {
    const fs = findings(rules, md("## Other", "y", "## Summary", "x"));
    assert.deepEqual(ruleIds(fs), ["first-section"]);
    assert.match(fs[0].message, /"## Summary" must be the first section, but "## Other" comes before it/);
  });
});

describe("last-section", () => {
  const rules = { last_section: "References", ignore_sections: ["Comments"] };
  test("allows the section last, ignoring a trailing ignored section", () => {
    assert.deepEqual(findings(rules, md("## A", "x", "## References", "r", "## Comments", "c")), []);
  });
  test("allows its absence", () => {
    assert.deepEqual(findings(rules, md("## A", "x")), []);
  });
  test("flags a section after it", () => {
    const fs = findings(rules, md("## References", "r", "## A", "x"));
    assert.deepEqual(ruleIds(fs), ["last-section"]);
    assert.match(fs[0].message, /"## References" must be the last section, but "## A" follows it/);
    assert.equal(fs[0].line, 3);
  });
});

describe("duplicate-section", () => {
  test("allows distinct names", () => {
    assert.deepEqual(findings({}, md("## A", "x", "## B", "y")), []);
  });
  test("flags a repeated name, compared case-insensitively", () => {
    const fs = findings({}, md("## Notes", "x", "## B", "y", "## NOTES:", "z"));
    assert.deepEqual(ruleIds(fs), ["duplicate-section"]);
    assert.equal(fs[0].message, 'section "## Notes" appears 2 times');
    assert.equal(fs[0].line, 5);
  });
  test("does not flag a repeated ignored section", () => {
    const text = md("## Comments", "x", "## Comments", "y");
    assert.deepEqual(findings({ ignore_sections: ["Comments"] }, text), []);
  });
});

describe("too-many-sections", () => {
  const rules = {
    required: ["Summary"],
    first_section: "Summary",
    last_section: "References",
    max_extra_sections: 2,
    ignore_sections: ["Comments"],
  };
  test("allows the limit; required, first, last and ignored sections are not counted", () => {
    const text = md("## Summary", "s", "## A", "a", "## B", "b", "## References", "r", "## Comments", "c");
    assert.deepEqual(findings(rules, text), []);
  });
  test("flags one over the limit and lists the technical sections", () => {
    const text = md("## Summary", "s", "## A", "a", "## B", "b", "## C", "c");
    const fs = findings(rules, text);
    assert.deepEqual(ruleIds(fs), ["too-many-sections"]);
    assert.equal(fs[0].message, '3 technical sections ("## A", "## B", "## C") exceed the limit of 2');
    assert.equal(fs[0].line, 7);
  });
});

describe("empty-section", () => {
  const rules = { required: ["Summary"] };
  test("allows a required section with text", () => {
    assert.deepEqual(findings(rules, md("## Summary", "Some text.")), []);
  });
  test("counts a fenced code block as content", () => {
    assert.deepEqual(findings(rules, md("## Summary", "```", "x = 1", "```")), []);
  });
  test("flags an empty required section", () => {
    const fs = findings(rules, md("## Summary", "", "## Other", "text"));
    assert.deepEqual(ruleIds(fs), ["empty-section"]);
    assert.equal(fs[0].line, 1);
  });
  test("flags a section holding only an unfilled template comment", () => {
    assert.deepEqual(ruleIds(findings(rules, md("## Summary", "<!-- one paragraph -->"))), ["empty-section"]);
  });
  test("counts a ### subheading with a word as content", () => {
    assert.deepEqual(findings(rules, md("## Summary", "### Detail", "## Other", "text")), []);
  });
  test("does not flag an empty optional section", () => {
    assert.deepEqual(findings(rules, md("## Summary", "x", "## Other")), []);
  });
});

describe("word-budget", () => {
  const rules = { max_words: 5 };
  test("allows exactly the budget; markers and bullets are not words", () => {
    // "Title" + "one two" + "three" + "four" = 5; "#", "##" and "-" are not words.
    assert.deepEqual(findings(rules, md("# Title", "## one two", "- three", "- four", "---")), []);
  });
  test("counts the words of a ## heading", () => {
    // "one two" + "three four" + "five six" = 6; without the heading words it would be 4.
    assert.deepEqual(ruleIds(findings(rules, md("## one two", "three four", "five six"))), ["word-budget"]);
  });
  test("flags one word over, with the count and the budget", () => {
    const fs = findings(rules, md("one two three four five six"));
    assert.deepEqual(ruleIds(fs), ["word-budget"]);
    assert.equal(fs[0].message, "6 words exceeds the budget of 5");
  });
  test("is off when max_words is absent", () => {
    assert.deepEqual(findings({}, "word ".repeat(5000)), []);
  });
});

describe("parsing", () => {
  test("headings inside ``` and ~~~ fences are not sections", () => {
    const rules = { required: ["Summary"] };
    const text = md("```md", "## Summary", "```", "~~~", "## Summary", "~~~");
    assert.deepEqual(ruleIds(findings(rules, text)), ["missing-section"]);
  });
  test("a fence closes only on the same character, at least as long", () => {
    const rules = { required: ["Summary"] };
    // The ~~~ and the shorter ``` inside the ```` fence do not close it.
    const text = md("````", "~~~", "```", "## Summary", "````", "## After", "x");
    assert.deepEqual(ruleIds(findings(rules, text)), ["missing-section"]);
    // A longer run of the other character does not close it either.
    assert.deepEqual(ruleIds(findings(rules, md("```", "~~~~", "## Summary", "```"))), ["missing-section"]);
  });
  test("a ``` line with a backtick in its info string is not a fence", () => {
    assert.deepEqual(findings({ required: ["Summary"] }, md("```foo`", "## Summary", "x")), []);
  });
  test("a backtick inside a comment in the info string still stops a ``` fence", () => {
    // The info string is literal text, so the "<!-- ` -->" does not hide its backtick.
    const text = md("```js <!-- ` -->", "## Summary", "one two");
    assert.deepEqual(findings({ required: ["Summary"] }, text), []);
    // "```js", "Summary", "one", "two": nothing after the line is hidden.
    assert.deepEqual(findings({ max_words: 4 }, text), []);
    assert.deepEqual(ruleIds(findings({ max_words: 3 }, text)), ["word-budget"]);
  });
  test("a fence opener with a comment in its info string still opens a fence", () => {
    const text = md("```js <!-- note -->", "## Summary", "```");
    assert.deepEqual(ruleIds(findings({ required: ["Summary"] }, text)), ["missing-section"]);
  });
  test("an unclosed fence runs to the end of the input", () => {
    const text = md("```", "## Summary", "words words words");
    assert.deepEqual(ruleIds(findings({ required: ["Summary"], max_words: 0 }, text)), ["missing-section"]);
  });
  test("words in fences, frontmatter and HTML comments are not counted", () => {
    const text = md(
      "---",
      "title: many words in the frontmatter here",
      "---",
      "one two",
      "```",
      "lots of code words that do not count",
      "```",
      "three <!-- hidden words --> four",
      "<!--",
      "## Summary",
      "a multi-line comment",
      "-->",
      "five",
    );
    assert.deepEqual(findings({ max_words: 5, required: ["Summary"], disable: ["missing-section"] }, text), []);
    assert.deepEqual(ruleIds(findings({ max_words: 4 }, text)), ["word-budget"]);
    assert.deepEqual(ruleIds(findings({ required: ["Summary"] }, text)), ["missing-section"]);
  });
  test("a heading in frontmatter is not a title", () => {
    const text = md("---", "# not a title", "---", "Body.");
    assert.deepEqual(ruleIds(findings({ require_title: true }, text)), ["title"]);
  });
  test("frontmatter may close with '...' and its fences may carry trailing spaces", () => {
    const text = md("--- ", "title: many words here", "tags:", "  - one", "- two", "...", "one");
    assert.deepEqual(findings({ max_words: 1 }, text), []);
  });
  test("an unterminated --- block is text, not frontmatter", () => {
    assert.deepEqual(ruleIds(findings({ max_words: 1 }, md("---", "one two"))), ["word-budget"]);
  });
  test("a leading --- before prose is a thematic break, not frontmatter", () => {
    const text = md("---", "Thesis here", "", "## Summary", "x", "", "---", "", "footer");
    assert.deepEqual(findings({ required: ["Summary"], require_thesis: true }, text), []);
    // A list item with no key before it is not YAML either: its words count.
    assert.deepEqual(ruleIds(findings({ max_words: 3 }, md("---", "- a point", "---", "one two"))), ["word-budget"]);
  });
  test("section names match case-insensitively, trimmed, trailing colon ignored", () => {
    const rules = { required: ["Summary", "Acceptance criteria"], first_section: "summary" };
    const text = md("##   SUMMARY:  ", "x", "## acceptance   Criteria ##", "y");
    assert.deepEqual(findings(rules, text), []);
  });
  test("### subheadings belong to the enclosing section", () => {
    const rules = { required: ["Summary", "Testing"], max_extra_sections: 0 };
    assert.deepEqual(findings(rules, md("## Summary", "### Detail", "x", "## Testing", "y")), []);
  });
  test("##hashtag and indented code are not headings", () => {
    assert.deepEqual(ruleIds(findings({ required: ["Summary"] }, md("##Summary", "    ## Summary"))), [
      "missing-section",
    ]);
  });
  test("a heading with no name is body text, not a section or title", () => {
    const rules = { require_title: true, required: ["Summary"], max_extra_sections: 0, require_thesis: true };
    const text = md("# T", "#", "thesis", "## Summary", "x", "##", "## ##", "## :", "more");
    assert.deepEqual(findings(rules, text), []);
  });
  test("CRLF input and a BOM are handled", () => {
    const text = "\uFEFF## Summary\r\nx\r\n## Summary\r\ny\r\n";
    assert.deepEqual(ruleIds(findings({ required: ["Summary"] }, text)), ["duplicate-section"]);
  });
});

describe("HTML comments", () => {
  const rules = { required: ["Summary"], require_thesis: true };
  test("a <!-- in an inline code span is code, not a comment", () => {
    assert.deepEqual(findings(rules, md("# T", "Thesis mentions `<!--` marker.", "## Summary", "x")), []);
  });
  test("an unclosed mid-line <!-- is text, not a comment that runs on", () => {
    assert.deepEqual(findings(rules, md("Thesis with a stray <!-- in it.", "## Summary", "x")), []);
    assert.deepEqual(ruleIds(findings({ max_words: 1 }, md("one <!-- two"))), ["word-budget"]);
  });
  test("a <!-- indented 4 spaces does not open a comment block", () => {
    assert.deepEqual(findings(rules, md("Thesis.", "    <!-- indented code", "## Summary", "x")), []);
  });
  test("a comment that closes mid-line is removed, with text either side kept", () => {
    const text = md("one <!-- two three --> four", "## Summary", "x");
    assert.deepEqual(findings({ ...rules, max_words: 4 }, text), []);
    assert.deepEqual(ruleIds(findings({ max_words: 3 }, text)), ["word-budget"]);
  });
  test("a comment in a code span is not removed", () => {
    const text = md("`<!-- a b c -->`", "## Summary", "x");
    assert.deepEqual(ruleIds(findings({ max_words: 4 }, text)), ["word-budget"]);
  });
  test("a line starting with <!-- opens a comment that hides headings until -->", () => {
    const text = md("Thesis.", "  <!-- template:", "## Summary", "fill in", "--> after");
    assert.deepEqual(ruleIds(findings(rules, text)), ["missing-section"]);
    assert.deepEqual(ruleIds(findings({ max_words: 1 }, text)), ["word-budget"]);
    assert.deepEqual(findings({ max_words: 2 }, text), []);
  });
  test("a comment-block line cannot open a fence or be a heading", () => {
    const text = md("Thesis.", "<!-- a --> ```", "## Summary", "x", "<!--", "c", "--> ## Other");
    assert.deepEqual(findings({ ...rules, max_extra_sections: 0 }, text), []);
  });
  test("a line with many code spans and comments is processed in linear time", () => {
    // 100k code spans with a comment between each: a quadratic span search
    // takes minutes here, so the subprocess timeout catches a regression.
    const text = "`a` <!-- x --> ".repeat(100000);
    const rules = rulesFile({ max_words: 99999 });
    const r = spawnSync(process.execPath, [SCRIPT, "--kind", "ticket", "--preset", "t", "--rules", rules, "--json"], {
      input: text,
      encoding: "utf8",
      timeout: 10000,
    });
    assert.equal(r.error, undefined, "checker timed out");
    assert.equal(r.status, 0, r.stderr);
    assert.deepEqual(ruleIds(JSON.parse(r.stdout).findings), ["word-budget"]);
  });
  test("a long comment hiding a code-span <!-- still breaks the budget under --strict", () => {
    const text = `${"word ".repeat(150)}with \`<!--\` in it ${"more ".repeat(146)}`;
    const r = run(["--kind", "comment", "--strict"], text);
    assert.equal(r.code, 1);
    assert.deepEqual(r.lines, ["comment: word-budget: 299 words exceeds the budget of 40"]);
  });
});

describe("ignore_sections", () => {
  const rules = {
    required: ["Summary"],
    last_section: "References",
    max_extra_sections: 0,
    max_words: 4, // "Summary x References r"; heading text counts
    ignore_sections: ["comments"],
  };
  test("an ignored section is excluded from every rule, words included", () => {
    const text = md("## Summary", "x", "## References", "r", "## Comments", "- many many many words in here", "## Comments");
    assert.deepEqual(findings(rules, text), []);
  });
  test("the last occurrence of the last section decides", () => {
    const r = { last_section: "References", disable: ["duplicate-section"] };
    assert.deepEqual(findings(r, md("## References", "r", "## A", "x", "## References", "s")), []);
  });
  test("a repeated technical section counts once", () => {
    const r = { max_extra_sections: 1, disable: ["duplicate-section"] };
    assert.deepEqual(findings(r, md("## A", "x", "## A", "y")), []);
  });
  test("the same section, not ignored, is flagged", () => {
    const text = md("## Summary", "x", "## References", "r", "## Comments", "- many many many words in here");
    const fs = findings({ ...rules, ignore_sections: [] }, text);
    assert.deepEqual(ruleIds(fs).sort(), ["last-section", "too-many-sections", "word-budget"]);
  });
});

describe("standard preset", () => {
  const good = md(
    "Adds a retry to the sync job so a transient network error no longer fails the run.",
    "",
    "## Summary",
    "The sync job retries three times with backoff.",
    "",
    "## Behavior",
    "A transient error is retried; a permanent one fails as before.",
    "",
    "## Design",
    "Backoff doubles from one second.",
    "",
    "## References",
    "- TKT-1",
    "",
    "## Comments",
    "- 2026-01-01T00:00:00Z someone: noted",
  );
  test("a ticket in the house format has no findings", () => {
    assert.deepEqual(preset("standard", "ticket", good), []);
  });
  test("the default preset is standard", () => {
    const r = run(["--kind", "pr", "--json"], good);
    assert.equal(r.code, 0, r.err);
    assert.deepEqual(JSON.parse(r.out).findings, []);
  });
  test("a pr in the problem-proposal shape is flagged", () => {
    const text = md("## Problem", "p", "## Proposal", "q");
    assert.deepEqual(ruleIds(preset("standard", "pr", text)), [
      "thesis",
      "missing-section",
      "missing-section",
    ]);
  });
  test("a note has no word budget; a ticket does", () => {
    const long = good.replace("Backoff doubles from one second.", "word ".repeat(900));
    assert.deepEqual(preset("standard", "note", long), []);
    assert.deepEqual(ruleIds(preset("standard", "ticket", long)), ["word-budget"]);
  });
  test("a comment is budgeted at 40 words", () => {
    assert.deepEqual(preset("standard", "comment", "word ".repeat(40)), []);
    const fs = preset("standard", "comment", "word ".repeat(41));
    assert.deepEqual(ruleIds(fs), ["word-budget"]);
    assert.equal(fs[0].message, "41 words exceeds the budget of 40");
  });
});

describe("problem-proposal preset", () => {
  test("a realistic board ticket has no findings", () => {
    const ticket = md(
      "---",
      "type: Story",
      "status: In Progress",
      "priority: Medium",
      "assignee: someone",
      "blocked_by: []",
      "---",
      "",
      "# list: page every search result",
      "",
      "Mirrors an upstream issue so this repo's own pipeline can select it.",
      "",
      "## Problem",
      "",
      "`tkt list` returns only the first page of results, so a query matching more",
      "than fifty tickets silently drops the rest.",
      "",
      "## Proposal",
      "",
      "Follow the pagination token until the backend reports the last page:",
      "",
      "```python",
      "while token:",
      "    page = fetch(token)",
      "```",
      "",
      "- Cap the loop at a configurable page count.",
      "- Surface a provider error when the cap is hit, rather than truncating.",
      "",
      "## Acceptance",
      "",
      "- A query matching 120 tickets returns 120.",
      "- The cap produces exit 3 with a clear message.",
      "",
      "## Comments",
      "- 2026-11-01T10:00:00Z someone: Plan posted.",
      "- 2026-11-02T10:00:00Z someone: " + "long comment words ".repeat(300),
    );
    assert.deepEqual(preset("problem-proposal", "ticket", ticket), []);
  });
  test("accepts 'Acceptance criteria' and flags the wrong order", () => {
    assert.deepEqual(
      preset("problem-proposal", "ticket", md("## Problem", "a", "## Proposal", "b", "## Acceptance criteria", "c")),
      [],
    );
    assert.deepEqual(
      ruleIds(preset("problem-proposal", "ticket", md("## Proposal", "b", "## Problem", "a", "## Acceptance", "c"))),
      ["section-order"],
    );
  });
  test("a pr from the open-pr body template has no findings", () => {
    const pr = md(
      "## Summary",
      "",
      "Pages every search result.",
      "",
      "## Ticket",
      "",
      "https://example.invalid/TKT-1",
      "",
      "## Changes",
      "- Follow the pagination token",
      "",
      "## Testing",
      "- unit tests",
      "",
      "## Checklist",
      "- [x] Tests added/updated",
    );
    assert.deepEqual(preset("problem-proposal", "pr", pr), []);
    assert.deepEqual(ruleIds(preset("problem-proposal", "pr", md("## Summary", "s", "## Testing", "t"))), [
      "missing-section",
    ]);
  });
  test("a note needs only a Summary; a comment is budgeted at 40 words", () => {
    assert.deepEqual(preset("problem-proposal", "note", md("## Summary", "x")), []);
    assert.deepEqual(ruleIds(preset("problem-proposal", "note", md("## Other", "x"))), ["missing-section"]);
    assert.deepEqual(ruleIds(preset("problem-proposal", "comment", "w ".repeat(41))), ["word-budget"]);
  });
});

describe("output and exit codes", () => {
  const bad = md("## Other", "x");
  const args = ["--kind", "ticket", "--preset", "problem-proposal"];

  test("findings go to stderr as '<kind>: <rule>: <message>', exit 0, stdout empty", () => {
    const r = run(args, bad);
    assert.equal(r.code, 0);
    assert.equal(r.out, "");
    assert.deepEqual(r.lines, [
      'ticket: missing-section: required section "## Problem" not found',
      'ticket: missing-section: required section "## Proposal" not found',
      'ticket: missing-section: required section "## Acceptance" or "## Acceptance criteria" not found',
    ]);
  });
  test("--strict with findings exits 1", () => {
    const r = run([...args, "--strict"], bad);
    assert.equal(r.code, 1);
    assert.equal(r.lines.length, 3);
  });
  test("--strict without findings exits 0 silently", () => {
    const r = run([...args, "--strict"], md("## Problem", "a", "## Proposal", "b", "## Acceptance", "c"));
    assert.equal(r.code, 0);
    assert.equal(r.err, "");
    assert.equal(r.out, "");
  });
  test("--json prints findings to stdout and still writes stderr", () => {
    const r = run([...args, "--json"], md("## Proposal", "b", "## Problem", "a", "## Acceptance", "c"));
    assert.equal(r.code, 0);
    assert.deepEqual(JSON.parse(r.out), {
      findings: [{ rule: "section-order", message: 'section "## Proposal" must come after "## Problem"', line: 1 }],
    });
    assert.deepEqual(r.lines, ['ticket: section-order: section "## Proposal" must come after "## Problem"']);
  });
  test("--json with no findings prints an empty list", () => {
    const r = run(["--kind", "comment", "--json"], "short");
    assert.equal(r.code, 0);
    assert.deepEqual(JSON.parse(r.out), { findings: [] });
  });
  test("the kind label in the stderr line follows --kind", () => {
    const r = run(["--kind", "comment"], "w ".repeat(41));
    assert.deepEqual(r.lines, ["comment: word-budget: 41 words exceeds the budget of 40"]);
  });
  test("--help exits 0 with usage on stdout", () => {
    const r = run(["--help"]);
    assert.equal(r.code, 0);
    assert.match(r.out, /^usage: /);
  });
});

describe("input sources", () => {
  const text = md("## Other", "x");
  const base = ["--kind", "ticket", "--preset", "problem-proposal", "--json"];
  const count = (r) => JSON.parse(r.out).findings.length;

  test("stdin when no FILE is given", () => {
    assert.equal(count(run(base, text)), 3);
  });
  test("stdin when FILE is '-'", () => {
    assert.equal(count(run([...base, "-"], text)), 3);
  });
  test("a FILE argument, ignoring stdin", () => {
    const file = tmpFile("ticket.md", text);
    assert.equal(count(run([...base, file], md("## Problem", "a", "## Proposal", "b", "## Acceptance", "c"))), 3);
  });
  test("empty input is checked, not an error", () => {
    const r = run(base, "");
    assert.equal(r.code, 0);
    assert.equal(count(r), 3);
  });
});

describe("usage errors exit 2", () => {
  const expect2 = (r, pattern) => {
    assert.equal(r.code, 2, `stdout=${r.out} stderr=${r.err}`);
    assert.equal(r.out, "");
    assert.match(r.err, /^error: /);
    assert.match(r.err, pattern);
  };

  test("missing --kind", () => expect2(run([], "x"), /--kind is required/));
  test("unknown kind", () => expect2(run(["--kind", "essay"], "x"), /unknown kind "essay"/));
  test("unknown preset, listing the available ones", () =>
    expect2(run(["--kind", "ticket", "--preset", "nope"], "x"), /unknown preset "nope" \(available: standard, problem-proposal\)/));
  test("unknown option", () => expect2(run(["--kind", "ticket", "--bogus"], "x"), /bogus/));
  test("more than one FILE", () => expect2(run(["--kind", "ticket", "a.md", "b.md"], "x"), /at most one input FILE/));
  test("missing input file", () =>
    expect2(run(["--kind", "ticket", join(tmpdir(), "tkt-no-such-file-29.md")], "x"), /cannot read .*ENOENT/));
  test("missing rules file", () =>
    expect2(run(["--kind", "ticket", "--rules", join(tmpdir(), "tkt-no-such-rules-29.json")], "x"), /cannot read rules file/));
  test("malformed rules JSON", () =>
    expect2(run(["--kind", "ticket", "--rules", tmpFile("bad.json", "{ not json")], "x"), /invalid JSON/));
  test("preset without the requested kind", () =>
    expect2(run(["--kind", "pr", "--preset", "t", "--rules", rulesFile({ max_words: 1 })], "x"), /preset "t" has no rules for kind "pr"/));

  const shapes = [
    ["top level not an object", [], /top level must be an object/],
    ["wrong version", { version: 2, presets: { t: {} } }, /"version" must be 1/],
    ["no presets", { version: 1, presets: {} }, /"presets" must be a non-empty object/],
    ["unknown top-level key", { version: 1, presets: { t: {} }, extra: 1 }, /unknown top-level key "extra"/],
    ["unknown kind", { version: 1, presets: { t: { essay: {} } } }, /unknown kind "essay"/],
    ["unknown rule key", { version: 1, presets: { t: { ticket: { max_word: 5 } } } }, /unknown key "max_word"/],
    ["wrong value type", { version: 1, presets: { t: { ticket: { max_words: "800" } } } }, /max_words must be a non-negative integer/],
    ["negative count", { version: 1, presets: { t: { ticket: { max_extra_sections: -1 } } } }, /max_extra_sections/],
    ["bad required entry", { version: 1, presets: { t: { ticket: { required: ["A", []] } } } }, /required must be/],
    ["unknown disabled rule", { version: 1, presets: { t: { ticket: { disable: ["no-such-rule"] } } } }, /disable must be/],
    ["non-string top-level $comment", { $comment: 1, version: 1, presets: { t: {} } }, /\$comment must be a string/],
    ["ignored and required", { version: 1, presets: { t: { ticket: { required: [["A", "notes:"]], ignore_sections: ["Notes"] } } } }, /"notes:" is in both required and ignore_sections/],
    ["ignored and first", { version: 1, presets: { t: { ticket: { first_section: "Comments", ignore_sections: ["comments"] } } } }, /both first_section/],
    ["ignored and last", { version: 1, presets: { t: { ticket: { last_section: ["Refs", "Comments"], ignore_sections: ["Comments"] } } } }, /both last_section/],
  ];
  for (const [name, doc, pattern] of shapes) {
    test(`invalid rules shape: ${name}`, () =>
      expect2(run(["--kind", "ticket", "--preset", "t", "--rules", tmpFile("shape.json", JSON.stringify(doc))], "x"), pattern));
  }
});
