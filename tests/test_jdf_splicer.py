"""JDF AST splicer tests."""

from __future__ import annotations

import copy
import unittest

from prompt_matrix.models.jdf import JDFDocumentTree, insert_node_after_anchor, splice_node, upsert_block_node


def _sample_tree() -> dict:
    return JDFDocumentTree(
        document_id="doc-1",
        truth_ledger={"revenue": 12_000_000},
        body=[
            {
                "type": "section",
                "id": "sec-1",
                "title": "Overview",
                "children": [
                    {
                        "type": "paragraph",
                        "id": "p-1",
                        "content": "Intro paragraph.",
                        "entities_referenced": [],
                        "meta": {},
                    },
                    {
                        "type": "paragraph",
                        "id": "p-2",
                        "content": "Target paragraph.",
                        "entities_referenced": ["revenue"],
                        "meta": {"locked": True},
                    },
                    {
                        "type": "callout",
                        "id": "c-1",
                        "variant": "insight",
                        "title": "Note",
                        "content": "Trailing callout.",
                    },
                ],
                "meta": {},
            }
        ],
    ).model_dump(mode="json")


class JDFSplicerTests(unittest.TestCase):
    def test_splice_replaces_target_only(self):
        tree = _sample_tree()
        original = copy.deepcopy(tree)
        replacement = {
            "type": "paragraph",
            "id": "p-2",
            "content": "Revised target paragraph.",
            "entities_referenced": ["revenue"],
            "meta": {"locked": True, "revised": True},
        }
        updated, found = splice_node(tree, "p-2", replacement)
        self.assertTrue(found)
        self.assertEqual(updated["body"][0]["children"][1]["content"], "Revised target paragraph.")
        self.assertEqual(updated["body"][0]["children"][0], original["body"][0]["children"][0])
        self.assertEqual(updated["body"][0]["children"][2], original["body"][0]["children"][2])
        self.assertEqual(tree["body"][0]["children"][1]["content"], "Target paragraph.")

    def test_splice_missing_id(self):
        tree = _sample_tree()
        updated, found = splice_node(tree, "missing", {"type": "paragraph", "id": "missing", "content": "x"})
        self.assertFalse(found)
        self.assertEqual(updated, tree)

    def test_insert_after_child(self):
        tree = _sample_tree()
        new_node = {"type": "paragraph", "id": "p-3", "content": "Docked.", "meta": {}}
        updated, ok = insert_node_after_anchor(tree, "p-1", new_node)
        self.assertTrue(ok)
        ids = [c["id"] for c in updated["body"][0]["children"]]
        self.assertEqual(ids[1], "p-3")

    def test_upsert_updates_existing(self):
        tree = _sample_tree()
        replacement = {
            "type": "paragraph",
            "id": "p-2",
            "content": "Updated via upsert.",
            "entities_referenced": [],
            "meta": {},
        }
        updated, found = upsert_block_node(tree, "p-2", replacement)
        self.assertTrue(found)
        self.assertEqual(updated["body"][0]["children"][1]["content"], "Updated via upsert.")

    def test_upsert_inserts_new(self):
        tree = _sample_tree()
        new_node = {"type": "paragraph", "id": "p-new", "content": "Inserted.", "meta": {}}
        updated, found = upsert_block_node(tree, "p-new", new_node, insert_after_id="p-1")
        self.assertFalse(found)
        ids = [c["id"] for c in updated["body"][0]["children"]]
        self.assertIn("p-new", ids)


if __name__ == "__main__":
    unittest.main()
