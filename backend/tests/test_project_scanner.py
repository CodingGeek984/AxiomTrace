"""Project scanner layout & linking tests.

Verifies the shared TB swimlane contract: every discovered file lands in one
of the four architecture tiers, coordinates stack the bands top-to-bottom
with rankSep 160 / nodeSep 100 (never the old flat horizontal rows), dense
bands wrap at ``MAX_TIER_COLUMNS``, and cross-file edges are real.
"""

from __future__ import annotations

from pathlib import Path

from services import project_scanner

# Enough service files to overflow one row (MAX_TIER_COLUMNS = 6).
FILES: dict[str, str] = {
    "src/components/Dashboard.jsx": (
        'import { fetchUsers } from "../api/users";\n'
        "export default function Dashboard() {\n"
        "  return <div>{fetchUsers}</div>;\n"
        "}\n"
    ),
    "src/components/Nav.jsx": "export default function Nav() { return <nav>nav</nav>; }\n",
    "src/api/users.py": (
        "from flask import Blueprint\n"
        "bp = Blueprint('users', __name__)\n"
    ),
    "src/api/orders.py": (
        "from flask import Blueprint\n"
        "bp = Blueprint('orders', __name__)\n"
    ),
    "src/services/auth.py": "def login(user):\n    return user\n",
    "src/services/billing.py": "from .audit import record\n\ndef charge(amount):\n    return amount\n",
    "src/services/notify.py": "def send(msg):\n    return msg\n",
    "src/services/report.py": "def build():\n    return 1\n",
    "src/services/audit.py": "def record():\n    return 1\n",
    "src/services/search.py": "def find(query):\n    return query\n",
    "src/services/cache.py": "def get(key):\n    return key\n",
    "src/services/queue.py": "def push(job):\n    return job\n",
    "src/models/user.py": "class User:\n    pass\n",
    "src/models/order.py": "class Order:\n    pass\n",
}


def _seed(root: Path) -> None:
    for relpath, content in FILES.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _scan(tmp_path: Path) -> dict:
    _seed(tmp_path)
    return project_scanner.scan_directory(str(tmp_path))


def _boxes(graph: dict) -> list[tuple[int, int, int, int]]:
    """Axis-aligned boxes using the shared node card size."""
    w = project_scanner.LAYOUT_NODE_WIDTH
    h = project_scanner.LAYOUT_NODE_HEIGHT
    return [(n["x"], n["y"], n["x"] + w, n["y"] + h) for n in graph["nodes"]]


class TestTierAssignment:
    def test_every_file_lands_in_one_of_four_tiers(self, tmp_path):
        graph = _scan(tmp_path)
        assert len(graph["nodes"]) == len(FILES)
        tiers = sorted({node["data"]["tier"] for node in graph["nodes"]})
        assert tiers == [1, 2, 3, 4]
        for node in graph["nodes"]:
            data = node["data"]
            assert data["tier"] in (1, 2, 3, 4)
            assert data["group"] in (
                "presentation",
                "api",
                "service",
                "data",
                "integration",
            )
            assert data["tier_label"]

    def test_path_rules_map_layers_to_expected_tiers(self, tmp_path):
        graph = _scan(tmp_path)
        by_label = {node["label"]: node for node in graph["nodes"]}
        assert by_label["Dashboard.jsx"]["data"]["tier"] == 1
        assert by_label["users.py"]["data"]["tier"] == 2
        assert by_label["auth.py"]["data"]["tier"] == 3
        assert by_label["user.py"]["data"]["tier"] == 4


class TestVerticalLayout:
    def test_tier_bands_stack_top_to_bottom_with_rank_sep(self, tmp_path):
        graph = _scan(tmp_path)
        height = project_scanner.LAYOUT_NODE_HEIGHT
        rank_sep = project_scanner.TIER_RANK_SEP

        tier_ys: dict[int, list[int]] = {}
        for node in graph["nodes"]:
            tier_ys.setdefault(node["data"]["tier"], []).append(node["y"])

        populated = sorted(tier_ys)
        assert populated == [1, 2, 3, 4]
        # T1 Client sits at the top, T4 Data at the bottom, each band a full
        # rankSep below the previous band's last row.
        for lower, upper in zip(populated, populated[1:]):
            band_bottom = max(tier_ys[lower]) + height
            assert min(tier_ys[upper]) - band_bottom >= rank_sep

    def test_cards_never_overlap(self, tmp_path):
        graph = _scan(tmp_path)
        boxes = _boxes(graph)
        for i, a in enumerate(boxes):
            for b in boxes[i + 1 :]:
                overlap = a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
                assert not overlap, f"boxes {a} and {b} overlap"

    def test_row_siblings_keep_node_sep_and_dense_bands_wrap(self, tmp_path):
        graph = _scan(tmp_path)
        node_sep = project_scanner.TIER_NODE_SEP
        width = project_scanner.LAYOUT_NODE_WIDTH
        max_columns = project_scanner.MAX_TIER_COLUMNS

        # Same-row siblings: horizontal gap >= nodeSep.
        by_row: dict[tuple[int, int], list[int]] = {}
        for node in graph["nodes"]:
            by_row.setdefault((node["data"]["tier"], node["y"]), []).append(node["x"])
        for xs in by_row.values():
            xs.sort()
            for left, right in zip(xs, xs[1:]):
                assert right - left - width >= node_sep

        # The 8-file service tier (tier 3) exceeds MAX_TIER_COLUMNS, so it
        # wraps into a second row instead of one long horizontal line.
        tier3 = [node for node in graph["nodes"] if node["data"]["tier"] == 3]
        rows = sorted({node["y"] for node in tier3})
        assert len(rows) == 2
        assert rows[1] - rows[0] == project_scanner.LAYOUT_NODE_HEIGHT + project_scanner.TIER_RANK_SEP
        max_x = max(node["x"] for node in tier3)
        assert max_x <= 80 + (max_columns - 1) * (width + node_sep)

    def test_layout_metadata_declares_the_tb_contract(self, tmp_path):
        graph = _scan(tmp_path)
        layout = graph["layout"]
        assert layout["rankDir"] == "TB"
        assert layout["rankSep"] == 160
        assert layout["nodeSep"] == 100
        assert layout["maxColumns"] == project_scanner.MAX_TIER_COLUMNS
        assert layout["fitView"] == {"duration": 800, "padding": 0.25, "maxZoom": 0.85}
        # Band tops ascend from T1 to T4.
        tops = [layout["tierY"][tier] for tier in sorted(layout["tierY"])]
        assert tops == sorted(tops)
        assert len(tops) == 4


class TestSourceAndEdges:
    def test_every_node_carries_its_full_source(self, tmp_path):
        graph = _scan(tmp_path)
        for node in graph["nodes"]:
            data = node["data"]
            assert data["code"].strip(), f"{node['id']} has no bound source"
            assert data["source"] == data["code"]
            assert data["file"]
            assert data["module"]

    def test_cross_file_import_edges_are_real(self, tmp_path):
        graph = _scan(tmp_path)
        labels = {node["id"]: node["label"] for node in graph["nodes"]}
        pairs = {
            (labels[edge["source"]], labels[edge["target"]], edge["kind"])
            for edge in graph["edges"]
        }
        # Relative import inside the service package resolves to a real edge.
        assert ("billing.py", "audit.py", "import") in pairs
        assert all(kind in {"import", "reference", "database"} for *_, kind in pairs)
