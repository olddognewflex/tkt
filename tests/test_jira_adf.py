"""Unit tests for the Jira Markdown -> ADF converter (adapters/jira.py).

Pure, offline, stdlib-only — no backend needed. Run from the repo root:

    python3 -m unittest tests.test_jira_adf
    python3 tests/test_jira_adf.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adapters.jira import _md_to_adf, _inline  # noqa: E402


def types(nodes):
    return [n.get("type") for n in nodes]


def marks(node):
    return sorted(m["type"] for m in node.get("marks", []))


def flatten_text(node):
    if node.get("type") == "text":
        return node.get("text", "")
    return "".join(flatten_text(c) for c in node.get("content", []) or [])


class DocShape(unittest.TestCase):
    def test_always_returns_a_doc(self):
        doc = _md_to_adf("")
        self.assertEqual(doc["type"], "doc")
        self.assertEqual(doc["version"], 1)
        # Empty input still yields a valid (empty) paragraph.
        self.assertEqual(types(doc["content"]), ["paragraph"])

    def test_plain_paragraph(self):
        doc = _md_to_adf("Just some prose.")
        self.assertEqual(types(doc["content"]), ["paragraph"])
        self.assertEqual(flatten_text(doc["content"][0]), "Just some prose.")

    def test_soft_wrapped_lines_join_into_one_paragraph(self):
        doc = _md_to_adf("line one\nline two")
        self.assertEqual(types(doc["content"]), ["paragraph"])
        self.assertEqual(flatten_text(doc["content"][0]), "line one line two")


class Blocks(unittest.TestCase):
    def test_headings_levels(self):
        doc = _md_to_adf("# H1\n\n### H3")
        h1, h3 = doc["content"]
        self.assertEqual((h1["type"], h1["attrs"]["level"]), ("heading", 1))
        self.assertEqual((h3["type"], h3["attrs"]["level"]), ("heading", 3))

    def test_hash_without_space_is_not_a_heading(self):
        doc = _md_to_adf("#nothashtag")
        self.assertEqual(types(doc["content"]), ["paragraph"])

    def test_bullet_list(self):
        doc = _md_to_adf("- a\n- b\n- c")
        self.assertEqual(types(doc["content"]), ["bulletList"])
        self.assertEqual(len(doc["content"][0]["content"]), 3)

    def test_ordered_list(self):
        doc = _md_to_adf("1. first\n2. second")
        self.assertEqual(types(doc["content"]), ["orderedList"])

    def test_nested_list_becomes_sublist(self):
        doc = _md_to_adf("- top\n  - child\n- top2")
        lst = doc["content"][0]
        first_item = lst["content"][0]
        # listItem holds a paragraph plus a nested list.
        self.assertIn("bulletList", types(first_item["content"]))

    def test_blockquote(self):
        doc = _md_to_adf("> quoted line")
        self.assertEqual(types(doc["content"]), ["blockquote"])
        self.assertEqual(flatten_text(doc["content"][0]), "quoted line")

    def test_blockquote_degrades_disallowed_children(self):
        # ADF blockquotes may only hold paragraph/list/codeBlock. Heading/rule/
        # nested-quote inside `>` must be coerced, never emitted verbatim (Jira
        # 400s on an invalid blockquote content model).
        allowed = {"paragraph", "bulletList", "orderedList", "codeBlock"}
        for src in ("> # Heading", "> ---", "> > nested"):
            bq = _md_to_adf(src)["content"][0]
            self.assertEqual(bq["type"], "blockquote")
            child_types = {c.get("type") for c in bq["content"]}
            self.assertTrue(child_types <= allowed, f"{src} -> {child_types}")
        # The heading's text survives as a paragraph.
        bq = _md_to_adf("> # Heading")["content"][0]
        self.assertEqual(flatten_text(bq), "Heading")

    def test_horizontal_rule(self):
        doc = _md_to_adf("above\n\n---\n\nbelow")
        self.assertEqual(types(doc["content"]), ["paragraph", "rule", "paragraph"])

    def test_fenced_code_block_keeps_language_and_literal_body(self):
        doc = _md_to_adf("```go\nfmt.Println(\"*not bold*\")\n```")
        cb = doc["content"][0]
        self.assertEqual(cb["type"], "codeBlock")
        self.assertEqual(cb["attrs"]["language"], "go")
        self.assertEqual(cb["content"][0]["text"], 'fmt.Println("*not bold*")')

    def test_list_does_not_swallow_following_paragraph(self):
        doc = _md_to_adf("- a\n- b\n\nAfter the list.")
        self.assertEqual(types(doc["content"]), ["bulletList", "paragraph"])

    def test_empty_list_item_is_skipped(self):
        # Middle line is a bare marker + space ("- ") = an empty item.
        doc = _md_to_adf("- a\n- \n- c")
        items = doc["content"][0]["content"]
        self.assertEqual(len(items), 2)
        self.assertEqual([flatten_text(i) for i in items], ["a", "c"])

    def test_all_empty_list_emits_no_list(self):
        # A list whose every item is empty must not emit an empty bulletList/
        # orderedList (content: []) — that is invalid ADF and Jira 400s on it.
        for src in ("- ", "1. ", "- \n- \n- "):
            doc = _md_to_adf(src)
            all_types = {n.get("type") for n in doc["content"]}
            self.assertNotIn("bulletList", all_types, src)
            self.assertNotIn("orderedList", all_types, src)
        # Same guard inside a blockquote.
        bq = _md_to_adf("> - ")["content"][0]
        self.assertEqual(bq["type"], "blockquote")
        self.assertNotIn("bulletList", {c.get("type") for c in bq["content"]})


class Inline(unittest.TestCase):
    def test_bold_and_italic(self):
        nodes = _inline("**bold** and *italic*")
        bold = next(n for n in nodes if n["text"] == "bold")
        ital = next(n for n in nodes if n["text"] == "italic")
        self.assertEqual(marks(bold), ["strong"])
        self.assertEqual(marks(ital), ["em"])

    def test_inline_code(self):
        nodes = _inline("call `doThing()` now")
        code = next(n for n in nodes if n["text"] == "doThing()")
        self.assertEqual(marks(code), ["code"])

    def test_strikethrough(self):
        nodes = _inline("~~gone~~")
        self.assertEqual(marks(nodes[0]), ["strike"])

    def test_link_becomes_link_mark(self):
        nodes = _inline("see [docs](https://example.com/x)")
        link = next(n for n in nodes if n["text"] == "docs")
        self.assertEqual(marks(link), ["link"])
        href = link["marks"][0]["attrs"]["href"]
        self.assertEqual(href, "https://example.com/x")

    def test_bold_link_keeps_both_marks(self):
        nodes = _inline("[**bold link**](https://e.co)")
        node = nodes[0]
        self.assertEqual(marks(node), ["link", "strong"])

    def test_unsafe_link_scheme_stays_plain_text(self):
        # javascript:/data:/protocol-relative must not become a link mark; the
        # text is preserved.
        for src in ("[x](javascript:alert(1))", "[y](data:text/html,<script>)",
                    "[z](//evil.com)"):
            nodes = _inline(src)
            self.assertTrue(all("link" not in marks(n) for n in nodes), src)
        # http/https/mailto plus schemeless relative/anchor links still render.
        for href in ("https://e.co", "mailto:a@b.co", "/rel", "#anchor",
                     "page.md", "../up"):
            nodes = _inline(f"[t]({href})")
            self.assertEqual(marks(nodes[0]), ["link"], href)
            self.assertEqual(nodes[0]["marks"][0]["attrs"]["href"], href)

    def test_double_star_is_strong_not_two_ems(self):
        nodes = _inline("**x**")
        self.assertEqual(len(nodes), 1)
        self.assertEqual(marks(nodes[0]), ["strong"])

    def test_unmatched_asterisk_stays_literal(self):
        # A lone glob star (services/*, go:*) must not start emphasis.
        for src in ("build services/* now", "run go:* tasks", "a * b"):
            nodes = _inline(src)
            self.assertEqual("".join(n["text"] for n in nodes), src)
            self.assertTrue(all(not n.get("marks") for n in nodes), src)

    def test_snake_case_underscores_stay_literal(self):
        nodes = _inline("field some_snake_case name")
        self.assertEqual("".join(n["text"] for n in nodes), "field some_snake_case name")
        self.assertTrue(all(not n.get("marks") for n in nodes))


if __name__ == "__main__":
    unittest.main()
