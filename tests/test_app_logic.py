from __future__ import annotations

import unittest

from thinktrellis.app import ThinkTrellisApp
from thinktrellis.model import MindMap


class _FakeTree:
    def __init__(self, selected: str) -> None:
        self.selected = selected

    def selection(self) -> tuple[str, ...]:
        return (self.selected,)


class _FakeCanvas:
    @staticmethod
    def world_box(node):
        return (node.x - 80, node.y - 30, node.x + 80, node.y + 30)


class _FakeValue:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = value


class _FakeRoot:
    def __init__(self) -> None:
        self.window_title = ""

    def after(self, _delay: int, _callback):
        return "after-id"

    def after_cancel(self, _identifier: str) -> None:
        return None

    def title(self, value: str) -> None:
        self.window_title = value


class TreeSelectionTests(unittest.TestCase):
    def _app_for(self, selected: str) -> tuple[ThinkTrellisApp, list[tuple[str, bool]]]:
        app = object.__new__(ThinkTrellisApp)
        app._syncing_tree = False
        app.map = MindMap.new()
        app.selected_id = app.map.root_id
        app.tree = _FakeTree(selected)
        calls: list[tuple[str, bool]] = []
        app.select_node = lambda node_id, focus_canvas=False: calls.append((node_id, focus_canvas))
        return app, calls

    def test_programmatic_selection_of_current_node_does_not_reselect_itself(self) -> None:
        app, calls = self._app_for(MindMap.new().root_id)
        # The selected id must come from this exact map, not a second map.
        app.tree.selected = app.selected_id

        app._on_tree_select(None)

        self.assertEqual(calls, [])

    def test_user_selection_of_another_node_is_processed_once(self) -> None:
        app = object.__new__(ThinkTrellisApp)
        app._syncing_tree = False
        app.map = MindMap.new()
        child = app.map.add_child(app.map.root_id, "Novo ramo")
        app.selected_id = app.map.root_id
        app.tree = _FakeTree(child.id)
        calls: list[tuple[str, bool]] = []
        app.select_node = lambda node_id, focus_canvas=False: calls.append((node_id, focus_canvas))

        app._on_tree_select(None)

        self.assertEqual(calls, [(child.id, True)])

    def test_conceptual_export_contains_the_relationship_label_and_arrow(self) -> None:
        app = object.__new__(ThinkTrellisApp)
        app.map = MindMap.new("Conceitos", map_type="conceptual")
        source = app.map.add_concept("Luz")
        target = app.map.add_concept("Prisma")
        app.map.add_relationship(source.id, target.id, "atravessa")
        app.map.arrange()
        app.canvas = _FakeCanvas()
        app.palette = {"canvas": "#FFFFFF", "connection": "#111111", "text": "#222222"}

        svg = app._build_svg()

        self.assertIn("atravessa", svg)
        self.assertIn('marker-end="url(#arrow)"', svg)

    def test_builtin_pdf_export_contains_map_content(self) -> None:
        app = object.__new__(ThinkTrellisApp)
        app.map = MindMap.new("Mapa para PDF")
        child = app.map.add_child(app.map.root_id, "Ramo (teste)")
        child.note = "Uma nota"
        app.canvas = _FakeCanvas()

        pdf = app._build_pdf()

        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertIn(b"Mapa para PDF", pdf)
        self.assertIn(b"Ramo \\(teste\\)", pdf)
        self.assertIn(b"xref", pdf)

    def test_undo_restores_the_previous_map_snapshot(self) -> None:
        app = object.__new__(ThinkTrellisApp)
        app.map = MindMap.new("Antes")
        app.selected_id = app.map.root_id
        app.theme_name = "dark"
        app._undo_stack = [app._history_snapshot()]
        app._redo_stack = []
        app.title_var = _FakeValue(app.map.title)
        app.status_var = _FakeValue()
        app.root = _FakeRoot()
        app._autosave_after = None
        app.dirty = False
        app._update_map_type_ui = lambda refresh=False: None
        selected: list[str] = []
        app.select_node = lambda node_id: selected.append(node_id)
        app._update_history_controls = lambda: None
        app.map.add_child(app.map.root_id, "Criada por engano")

        app.undo()

        self.assertEqual(len(app.map.nodes), 1)
        self.assertEqual(selected, [app.map.root_id])
        self.assertEqual(len(app._redo_stack), 1)

    def test_tab_shortcut_uses_the_creation_action_when_the_map_has_focus(self) -> None:
        app = object.__new__(ThinkTrellisApp)
        app._canvas_shortcut_allowed = lambda: True
        calls: list[str] = []
        app.add_child = lambda: calls.append("child")

        result = app._shortcut_child(None)

        self.assertEqual(result, "break")
        self.assertEqual(calls, ["child"])


if __name__ == "__main__":
    unittest.main()
