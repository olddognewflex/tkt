#!/usr/bin/env node
// check-artifact-format.mjs: mechanical format checks for a markdown artifact
// (ticket body, PR body, note, or comment) against a named rules preset.
//
// Usage:
//   check-artifact-format.mjs --kind ticket|pr|note|comment [--preset NAME]
//       [--rules FILE] [--strict] [--json] [FILE | -]
//
// Input is FILE, or stdin when FILE is "-" or absent. The preset defaults to
// "standard"; the rules file defaults to artifact-format.rules.json next to
// this script. Pure Node stdlib (Node >= 20), no dependencies.
//
// Output contract (tkt's transition/comment gate parses this; keep it stable):
//   - One line per finding on stderr: "<kind>: <rule-id>: <message>".
//     Nothing goes to stdout unless --json is given, which prints
//     {"findings":[{"rule","message","line"?}]} to stdout (stderr lines are
//     still written). "line" is 1-based in the input as given.
//   - Exit 0: no findings, or findings without --strict (advisory).
//     Exit 1: findings with --strict.
//     Exit 2: usage error, unreadable input, unknown kind or preset, or a
//     missing or invalid rules file. The message goes to stderr as "error: ...".
//     Callers must not parse stderr lines on exit 2: they are not findings,
//     and a usage error prints "error: ..." followed by a usage line.
//
// What it checks (rule ids, each driven by a key of the kind's rules):
//   title              require_title: exactly one "# " title line.
//   thesis             require_thesis: a body line (not a heading of any
//                      level) with at least one word before the first "## "
//                      section. The "# " title and "###" subheadings do not
//                      count; neither does anything skipped below.
//   missing-section    required: each entry must appear. An entry is a name
//                      or a list of alternative names (any one satisfies it).
//   section-order      required: the required sections that are present must
//                      appear in the listed order.
//   first-section      first_section: if present, it is the first section.
//   last-section       last_section: if present, it is the last section.
//   duplicate-section  always: no section name appears twice.
//   too-many-sections  max_extra_sections: the number of distinct "technical"
//                      sections (those not named by required, first_section or
//                      last_section) must not exceed it.
//   empty-section      required: a required section that is present has body
//                      content: a body line with a word, a fenced code block
//                      (even an empty one), or a "###"-or-deeper subheading
//                      with a word in it. So "## Summary" holding only
//                      "### Detail" is not empty.
//   word-budget        max_words: the word count must not exceed it.
// Every rule ignores the sections named in ignore_sections (e.g. "Comments"):
// they are not sections for any rule and their bodies are not counted. Any
// rule can be switched off for a kind by listing its id in "disable". The
// "standard" and "problem-proposal" note kinds set no max_words on purpose.
//
// How the input is read:
//   - A leading YAML frontmatter block ("---" ... "---" or "...") is skipped,
//     but only when every non-blank line inside it looks like YAML: "key:" or
//     "key: value" (optionally indented), a "- item" or an indented
//     continuation after a key, or a "#" comment. Otherwise the first "---"
//     is a thematic break and the text after it is the document.
//   - Fenced code blocks (``` or ~~~, closed by the same fence character at
//     least as long) are skipped for heading detection and word counts. A
//     ``` line whose info string holds a backtick is not a fence.
//   - HTML comments are skipped the same way. A line starting with "<!--"
//     (up to 3 spaces of indent) opens a comment block that runs to the line
//     holding "-->"; neither that line nor the closing one can open a fence
//     or be a heading. Anywhere else, only a "<!-- ... -->" that closes on
//     the same line is removed, and a "<!--" inside an inline code span is
//     code, not a comment; an unclosed mid-line "<!--" is plain text.
//   - Headings are ATX lines ("#" to "######" plus a space, up to 3 spaces of
//     indent) outside fences and comments. "# " is a title, "## " starts a
//     section, deeper levels are body text of the enclosing section. A
//     heading with no name (a bare "##", or only "#"s or ":") is body text.
//   - Section names compare case-insensitively, trimmed, whitespace collapsed,
//     with a trailing ":" ignored ("## summary:" matches "Summary").
//   - A word is a whitespace-separated token containing a letter or digit, so
//     bullet markers, table pipes and "---" rules are not words. Heading text
//     counts; the "#" markers do not.
//
// What it deliberately does NOT check: whether the prose is good, its tone,
// whether it is compressed or terse (that is check-prose-register's job),
// whether anything it says is true or matches the code, link targets,
// markdown validity beyond what heading detection needs, setext ("===")
// headings, or indented code blocks. It never edits the input.
//
// Rules file shape (validated on load; a bad file is exit 2):
//   {
//     "version": 1,
//     "presets": {
//       "<preset>": {
//         "<kind>": {                     // kind: ticket | pr | note | comment
//           "require_title": bool,        // default false
//           "require_thesis": bool,       // default false
//           "required": [name | [name, ...], ...],
//           "first_section": name | [name, ...],
//           "last_section": name | [name, ...],
//           "max_extra_sections": int >= 0,
//           "max_words": int >= 0,
//           "ignore_sections": [name, ...],
//           "disable": [rule-id, ...]
//         }
//       }
//     }
//   }
// Every key is optional; an omitted key disables the rule it drives. A
// "$comment" string is allowed at the top level, in a preset and in a kind.
// Unknown keys are errors, so a typo cannot silently disable a rule, and so
// is a name in ignore_sections that required, first_section or last_section
// also names (the ignored section could never satisfy them).

import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const KINDS = ["ticket", "pr", "note", "comment"];
const RULE_IDS = [
  "title",
  "thesis",
  "missing-section",
  "section-order",
  "first-section",
  "last-section",
  "duplicate-section",
  "too-many-sections",
  "empty-section",
  "word-budget",
];
const DEFAULT_PRESET = "standard";
const DEFAULT_RULES = join(
  dirname(fileURLToPath(import.meta.url)),
  "artifact-format.rules.json",
);
const USAGE =
  "usage: check-artifact-format.mjs --kind ticket|pr|note|comment " +
  "[--preset NAME] [--rules FILE] [--strict] [--json] [FILE | -]";

class UsageError extends Error {}

// ---------------------------------------------------------------- rules file

const isPlainObject = (v) =>
  v !== null && typeof v === "object" && !Array.isArray(v);
const isName = (v) => typeof v === "string" && v.trim() !== "";
const isSpec = (v) =>
  isName(v) || (Array.isArray(v) && v.length > 0 && v.every(isName));
const isCount = (v) => Number.isInteger(v) && v >= 0;
const specNames = (spec) => (Array.isArray(spec) ? spec : [spec]);

const KIND_KEYS = {
  $comment: [(v) => typeof v === "string", "a string"],
  require_title: [(v) => typeof v === "boolean", "true or false"],
  require_thesis: [(v) => typeof v === "boolean", "true or false"],
  required: [
    (v) => Array.isArray(v) && v.every(isSpec),
    "a list of section names or lists of alternative names",
  ],
  first_section: [isSpec, "a section name or a list of alternative names"],
  last_section: [isSpec, "a section name or a list of alternative names"],
  max_extra_sections: [isCount, "a non-negative integer"],
  max_words: [isCount, "a non-negative integer"],
  ignore_sections: [
    (v) => Array.isArray(v) && v.every(isName),
    "a list of section names",
  ],
  disable: [
    (v) => Array.isArray(v) && v.every((id) => RULE_IDS.includes(id)),
    `a list of rule ids (${RULE_IDS.join(", ")})`,
  ],
};

// Returns an error message, or null when the rules document is valid.
function validateRules(doc) {
  if (!isPlainObject(doc)) return "top level must be an object";
  for (const key of Object.keys(doc)) {
    if (!["version", "presets", "$comment"].includes(key)) {
      return `unknown top-level key "${key}"`;
    }
  }
  if (doc.$comment !== undefined && typeof doc.$comment !== "string") {
    return "$comment must be a string";
  }
  if (doc.version !== 1) return '"version" must be 1';
  if (!isPlainObject(doc.presets) || Object.keys(doc.presets).length === 0) {
    return '"presets" must be a non-empty object';
  }
  for (const [preset, kinds] of Object.entries(doc.presets)) {
    const where = `presets.${preset}`;
    if (!isPlainObject(kinds)) return `${where} must be an object`;
    for (const [kind, rules] of Object.entries(kinds)) {
      if (kind === "$comment") {
        if (typeof rules !== "string") return `${where}.$comment must be a string`;
        continue;
      }
      if (!KINDS.includes(kind)) {
        return `${where}: unknown kind "${kind}" (expected ${KINDS.join(", ")})`;
      }
      if (!isPlainObject(rules)) return `${where}.${kind} must be an object`;
      for (const [key, value] of Object.entries(rules)) {
        const check = KIND_KEYS[key];
        if (!check) return `${where}.${kind}: unknown key "${key}"`;
        if (!check[0](value)) return `${where}.${kind}.${key} must be ${check[1]}`;
      }
      const ignored = new Set((rules.ignore_sections ?? []).map(normalizeName));
      for (const key of ["required", "first_section", "last_section"]) {
        if (rules[key] === undefined) continue;
        const names = key === "required" ? rules[key].flatMap(specNames) : specNames(rules[key]);
        const clash = names.find((n) => ignored.has(normalizeName(n)));
        if (clash !== undefined) {
          return `${where}.${kind}: "${clash.trim()}" is in both ${key} and ignore_sections`;
        }
      }
    }
  }
  return null;
}

async function loadRules(path) {
  let raw;
  try {
    raw = await readFile(path, "utf8");
  } catch (err) {
    throw new UsageError(`cannot read rules file ${path}: ${err.code ?? err.message}`);
  }
  let doc;
  try {
    doc = JSON.parse(raw);
  } catch (err) {
    throw new UsageError(`rules file ${path}: invalid JSON: ${err.message}`);
  }
  const problem = validateRules(doc);
  if (problem) throw new UsageError(`rules file ${path}: ${problem}`);
  return doc;
}

// ------------------------------------------------------------------- parsing

function normalizeName(name) {
  return name.trim().replace(/:+$/, "").trim().replace(/\s+/g, " ").toLowerCase();
}

function countWords(text) {
  let n = 0;
  for (const token of text.split(/\s+/)) {
    if (/[\p{L}\p{N}]/u.test(token)) n += 1;
  }
  return n;
}

const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})(.*)$/;
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/;
const ATX = /^ {0,3}(#{1,6})(?=[ \t]|$)(.*)$/;
const COMMENT_BLOCK = /^ {0,3}<!--/;
const YAML_KEY = /^[ \t]*[\w.-]+[ \t]*:(?:[ \t].*)?$/;
const YAML_ITEM = /^[ \t]*-(?:[ \t].*)?$/;
const YAML_COMMENT = /^[ \t]*#/;

// [start, end) offsets of the inline code spans on one line: a run of N
// backticks closed by the next run of exactly N. A run with no closer is
// literal text, and the search goes on from the next run.
function codeSpans(line) {
  const runs = [...line.matchAll(/`+/g)];
  const spans = [];
  for (let a = 0; a < runs.length; a++) {
    const b = runs.findIndex((r, k) => k > a && r[0].length === runs[a][0].length);
    if (b < 0) continue;
    spans.push([runs[a].index, runs[b].index + runs[b][0].length]);
    a = b;
  }
  return spans;
}

// Removes the "<!-- ... -->" comments that open and close on this line. A
// "<!--" inside a code span, or with no "-->" after it, is left as text.
function stripInlineComments(line) {
  const spans = codeSpans(line);
  const inSpan = (i) => spans.some(([s, e]) => i >= s && i < e);
  let out = "";
  let from = 0;
  let start = line.indexOf("<!--");
  while (start >= 0) {
    if (inSpan(start)) {
      start = line.indexOf("<!--", start + 1);
      continue;
    }
    const end = line.indexOf("-->", start + 4);
    if (end < 0) break;
    out += `${line.slice(from, start)} `;
    from = end + 3;
    start = line.indexOf("<!--", from);
  }
  return out + line.slice(from);
}

// Removes HTML comments from one line. Returns the remaining text, whether a
// comment block is still open after it, and whether the line belongs to a
// comment block (an HTML block: it cannot open a fence or be a heading).
function stripComments(line, inComment) {
  let rest;
  if (inComment) rest = line;
  else if (COMMENT_BLOCK.test(line)) rest = line.slice(line.indexOf("<!--") + 4);
  else return { text: stripInlineComments(line), inComment: false, block: false };
  const end = rest.indexOf("-->");
  if (end < 0) return { text: "", inComment: true, block: true };
  return { text: stripInlineComments(rest.slice(end + 3)), inComment: false, block: true };
}

// Index of the first line after a leading YAML frontmatter block, or 0 when
// there is none: no closing "---"/"...", or a line between the fences that
// does not look like YAML (then the opening "---" is a thematic break).
function frontmatterEnd(lines) {
  if (lines[0]?.trimEnd() !== "---") return 0;
  let sawKey = false;
  for (let j = 1; j < lines.length; j++) {
    const line = lines[j].trimEnd();
    if (line === "---" || line === "...") return j + 1;
    if (line.trim() === "" || YAML_COMMENT.test(line)) continue;
    if (YAML_KEY.test(line)) sawKey = true;
    else if (!(sawKey && (YAML_ITEM.test(line) || /^[ \t]/.test(line)))) return 0;
  }
  return 0;
}

// Splits markdown into a title list, a preamble flag and "##" sections.
// Every region (preamble, each section, text after a later "#" line) keeps
// its own word count so ignored sections can be left out of the total.
function parse(source) {
  const lines = source.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n").split("\n");
  let i = frontmatterEnd(lines);

  const titles = [];
  const sections = [];
  const regions = [];
  const newRegion = (extra = {}) => {
    const r = { words: 0, hasContent: false, ...extra };
    regions.push(r);
    return r;
  };
  let current = newRegion();
  let sawSection = false;
  let thesis = false;
  let fence = null;
  let inComment = false;

  for (; i < lines.length; i++) {
    const lineNo = i + 1;
    const raw = lines[i];

    if (fence) {
      const m = FENCE_CLOSE.exec(raw);
      if (m && m[1][0] === fence.ch && m[1].length >= fence.len) fence = null;
      continue;
    }

    const stripped = stripComments(raw, inComment);
    inComment = stripped.inComment;
    const text = stripped.text;

    const open = stripped.block ? null : FENCE_OPEN.exec(text);
    if (open && !(open[1][0] === "`" && open[2].includes("`"))) {
      fence = { ch: open[1][0], len: open[1].length };
      current.hasContent = true;
      continue;
    }

    const heading = stripped.block ? null : ATX.exec(text);
    const content = heading?.[2].trim().replace(/(?:^|[ \t]+)#+$/, "").trim();
    if (heading && normalizeName(content) !== "") {
      const level = heading[1].length;
      const words = countWords(content);
      if (level === 1) {
        titles.push({ line: lineNo, text: content });
        current = newRegion();
        current.words += words;
      } else if (level === 2) {
        sawSection = true;
        current = newRegion({
          name: content,
          norm: normalizeName(content),
          line: lineNo,
        });
        current.words += words;
        sections.push(current);
      } else {
        current.words += words;
        if (words > 0) current.hasContent = true;
      }
      continue;
    }

    const words = countWords(text);
    current.words += words;
    if (words > 0) {
      current.hasContent = true;
      if (!sawSection) thesis = true;
    }
  }

  return { titles, sections, regions, thesis };
}

// --------------------------------------------------------------------- rules

const specNorms = (spec) => specNames(spec).map(normalizeName);
const showSpec = (spec) =>
  specNames(spec)
    .map((n) => `"## ${n.trim()}"`)
    .join(" or ");
const showSection = (s) => `"## ${s.name}"`;

function check(source, rules) {
  const doc = parse(source);
  const off = new Set(rules.disable ?? []);
  const on = (id) => !off.has(id);
  const ignored = new Set((rules.ignore_sections ?? []).map(normalizeName));
  const secs = doc.sections.filter((s) => !ignored.has(s.norm));
  const required = rules.required ?? [];
  const findings = [];
  const add = (rule, message, line) =>
    findings.push(line === undefined ? { rule, message } : { rule, message, line });
  const firstIndex = (spec) => {
    const norms = specNorms(spec);
    return secs.findIndex((s) => norms.includes(s.norm));
  };

  if (rules.require_title && on("title") && doc.titles.length !== 1) {
    if (doc.titles.length === 0) add("title", 'no "# " title line found');
    else {
      add(
        "title",
        `found ${doc.titles.length} "# " title lines, expected exactly 1`,
        doc.titles[1].line,
      );
    }
  }

  if (rules.require_thesis && on("thesis") && !doc.thesis) {
    add("thesis", 'no thesis: expected prose before the first "##" section', secs[0]?.line);
  }

  const positions = required.map(firstIndex);

  if (on("missing-section")) {
    required.forEach((spec, k) => {
      if (positions[k] < 0) add("missing-section", `required section ${showSpec(spec)} not found`);
    });
  }

  if (on("section-order")) {
    let maxPos = -1;
    let maxSpec = null;
    required.forEach((spec, k) => {
      const pos = positions[k];
      if (pos < 0) return;
      if (pos < maxPos) {
        add(
          "section-order",
          `section ${showSection(secs[pos])} must come after ${showSpec(maxSpec)}`,
          secs[pos].line,
        );
      } else {
        maxPos = pos;
        maxSpec = spec;
      }
    });
  }

  if (rules.first_section !== undefined && on("first-section")) {
    const pos = firstIndex(rules.first_section);
    if (pos > 0) {
      add(
        "first-section",
        `section ${showSection(secs[pos])} must be the first section, ` +
          `but ${showSection(secs[0])} comes before it`,
        secs[pos].line,
      );
    }
  }

  if (rules.last_section !== undefined && on("last-section")) {
    const norms = specNorms(rules.last_section);
    let pos = -1;
    secs.forEach((s, k) => {
      if (norms.includes(s.norm)) pos = k;
    });
    if (pos >= 0 && pos < secs.length - 1) {
      add(
        "last-section",
        `section ${showSection(secs[pos])} must be the last section, ` +
          `but ${showSection(secs[pos + 1])} follows it`,
        secs[pos + 1].line,
      );
    }
  }

  if (on("duplicate-section")) {
    const seen = new Map();
    for (const s of secs) {
      const prior = seen.get(s.norm);
      if (!prior) seen.set(s.norm, { first: s, count: 1 });
      else {
        prior.count += 1;
        prior.second ??= s;
      }
    }
    for (const { first, count, second } of seen.values()) {
      if (count > 1) {
        add("duplicate-section", `section ${showSection(first)} appears ${count} times`, second.line);
      }
    }
  }

  if (rules.max_extra_sections !== undefined && on("too-many-sections")) {
    const named = new Set(required.flatMap(specNorms));
    if (rules.first_section !== undefined) specNorms(rules.first_section).forEach((n) => named.add(n));
    if (rules.last_section !== undefined) specNorms(rules.last_section).forEach((n) => named.add(n));
    const extras = [];
    const seen = new Set();
    for (const s of secs) {
      if (named.has(s.norm) || seen.has(s.norm)) continue;
      seen.add(s.norm);
      extras.push(s);
    }
    if (extras.length > rules.max_extra_sections) {
      add(
        "too-many-sections",
        `${extras.length} technical sections (${extras.map(showSection).join(", ")}) ` +
          `exceed the limit of ${rules.max_extra_sections}`,
        extras[rules.max_extra_sections].line,
      );
    }
  }

  if (on("empty-section")) {
    required.forEach((spec, k) => {
      const s = secs[positions[k]];
      if (s && !s.hasContent) add("empty-section", `required section ${showSection(s)} is empty`, s.line);
    });
  }

  if (rules.max_words !== undefined && on("word-budget")) {
    const words = doc.regions
      .filter((r) => r.norm === undefined || !ignored.has(r.norm))
      .reduce((sum, r) => sum + r.words, 0);
    if (words > rules.max_words) {
      add("word-budget", `${words} words exceeds the budget of ${rules.max_words}`);
    }
  }

  return findings;
}

// ----------------------------------------------------------------------- CLI

async function readInput(file) {
  if (file === undefined || file === "-") {
    if (process.stdin.isTTY) {
      throw new UsageError("no input: pass FILE, or pipe the artifact to stdin");
    }
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    return Buffer.concat(chunks).toString("utf8");
  }
  try {
    return await readFile(file, "utf8");
  } catch (err) {
    throw new UsageError(`cannot read ${file}: ${err.code ?? err.message}`);
  }
}

async function main(argv) {
  let parsed;
  try {
    parsed = parseArgs({
      args: argv,
      allowPositionals: true,
      options: {
        kind: { type: "string" },
        preset: { type: "string", default: DEFAULT_PRESET },
        rules: { type: "string", default: DEFAULT_RULES },
        strict: { type: "boolean", default: false },
        json: { type: "boolean", default: false },
        help: { type: "boolean", short: "h", default: false },
      },
    });
  } catch (err) {
    throw new UsageError(`${err.message}\n${USAGE}`);
  }
  const { values, positionals } = parsed;
  if (values.help) {
    process.stdout.write(`${USAGE}\n`);
    return 0;
  }
  if (values.kind === undefined) throw new UsageError(`--kind is required\n${USAGE}`);
  if (!KINDS.includes(values.kind)) {
    throw new UsageError(`unknown kind "${values.kind}" (expected ${KINDS.join(", ")})`);
  }
  if (positionals.length > 1) {
    throw new UsageError(`expected at most one input FILE, got ${positionals.length}\n${USAGE}`);
  }

  const doc = await loadRules(values.rules);
  const preset = Object.hasOwn(doc.presets, values.preset) ? doc.presets[values.preset] : undefined;
  if (!preset) {
    throw new UsageError(
      `unknown preset "${values.preset}" (available: ${Object.keys(doc.presets).join(", ")})`,
    );
  }
  const rules = Object.hasOwn(preset, values.kind) ? preset[values.kind] : undefined;
  if (!rules) {
    throw new UsageError(`preset "${values.preset}" has no rules for kind "${values.kind}"`);
  }

  const source = await readInput(positionals[0]);
  const findings = check(source, rules);

  if (findings.length) {
    process.stderr.write(findings.map((f) => `${values.kind}: ${f.rule}: ${f.message}\n`).join(""));
  }
  if (values.json) process.stdout.write(`${JSON.stringify({ findings })}\n`);
  return findings.length && values.strict ? 1 : 0;
}

try {
  process.exitCode = await main(process.argv.slice(2));
} catch (err) {
  const message = err instanceof UsageError ? err.message : `internal: ${err.stack ?? err}`;
  process.stderr.write(`error: ${message}\n`);
  process.exitCode = 2;
}
