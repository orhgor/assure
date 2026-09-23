"""2D0b — dump one anchored node of the live demo tree verbatim, for the fixture."""
import json
import sys

sys.path.insert(0, "/home/ubuntu/assure-prototype")
sys.path.insert(0, "/home/ubuntu/assure-prototype/prompt_matrix")

from prompt_matrix.db.jdf_repository import fetch_latest_jdf_or_empty  # noqa: E402

pid = sys.argv[1] if len(sys.argv) > 1 else "demo-3235f5"
tree = fetch_latest_jdf_or_empty(pid)
for section in tree.get("body") or []:
    for node in section.get("children") or []:
        if node.get("type") == "paragraph" and node.get("provenance"):
            print(json.dumps(node, indent=1, ensure_ascii=False))
            sys.exit(0)
