from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from thinktrellis.model import MindMap


class MindMapTests(unittest.TestCase):
    def test_new_map_has_a_root(self) -> None:
        mind_map = MindMap.new("Planejamento")

        self.assertEqual(mind_map.title, "Planejamento")
        self.assertEqual(mind_map.root.text, "Ideia principal")
        self.assertIsNone(mind_map.root.parent_id)

    def test_creates_children_and_a_sibling(self) -> None:
        mind_map = MindMap.new()
        first = mind_map.add_child(mind_map.root_id, "Primeiro ramo")
        second = mind_map.add_sibling(first.id, "Segundo ramo")
        child = mind_map.add_child(first.id, "Detalhe")

        self.assertEqual(first.parent_id, mind_map.root_id)
        self.assertEqual(second.parent_id, mind_map.root_id)
        self.assertEqual(child.parent_id, first.id)
        self.assertEqual(len(mind_map.nodes), 4)

    def test_arrange_places_root_at_center_and_branches_on_both_sides(self) -> None:
        mind_map = MindMap.new()
        for name in ("A", "B", "C", "D"):
            mind_map.add_child(mind_map.root_id, name)
        mind_map.arrange()

        children = mind_map.children(mind_map.root_id)
        self.assertEqual((mind_map.root.x, mind_map.root.y), (0, 0))
        self.assertTrue(any(child.x < 0 for child in children))
        self.assertTrue(any(child.x > 0 for child in children))

    def test_save_and_load_preserves_notes_and_theme(self) -> None:
        mind_map = MindMap.new("Resumo de estudo")
        child = mind_map.add_child(mind_map.root_id, "Luz")
        child.note = "Anotar o experimento do DVD e da vela."
        child.collapsed = True

        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "estudo.thinktrellis"
            mind_map.save(target, theme="light")
            reopened, theme = MindMap.load(target)

        restored = reopened.nodes[child.id]
        self.assertEqual(reopened.title, "Resumo de estudo")
        self.assertEqual(restored.note, child.note)
        self.assertTrue(restored.collapsed)
        self.assertEqual(theme, "light")

    def test_deleting_branch_removes_its_descendants(self) -> None:
        mind_map = MindMap.new()
        branch = mind_map.add_child(mind_map.root_id, "Ramo")
        descendant = mind_map.add_child(branch.id, "Filho")

        removed = mind_map.delete_branch(branch.id)

        self.assertEqual(set(removed), {branch.id, descendant.id})
        self.assertEqual(set(mind_map.nodes), {mind_map.root_id})

    def test_conceptual_map_creates_labelled_relationships(self) -> None:
        mind_map = MindMap.new("Ecossistema", map_type="conceptual")
        producer = mind_map.add_concept("Plantas")
        consumer = mind_map.add_concept("Herbívoros")
        relationship = mind_map.add_relationship(producer.id, consumer.id, "alimentam")

        self.assertEqual(mind_map.map_type, "conceptual")
        self.assertEqual(mind_map.root.text, "Conceito central")
        self.assertEqual(relationship.label, "alimentam")
        self.assertEqual(mind_map.relationships_for(producer.id), [relationship])

    def test_conceptual_map_round_trip_and_delete_removes_relationships(self) -> None:
        mind_map = MindMap.new("Relações", map_type="conceptual")
        first = mind_map.add_concept("A")
        second = mind_map.add_concept("B")
        mind_map.add_relationship(first.id, second.id, "influencia")
        mind_map.arrange()

        self.assertLess(mind_map.root.y, first.y)
        self.assertEqual(len(mind_map.relationships), 1)

        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "conceitual.thinktrellis"
            mind_map.save(target)
            reopened, _theme = MindMap.load(target)

        self.assertEqual(reopened.map_type, "conceptual")
        self.assertEqual(len(reopened.relationships), 1)
        reopened.delete_branch(first.id)
        self.assertEqual(len(reopened.relationships), 0)

    def test_relationship_selector_uses_the_text_written_in_each_concept(self) -> None:
        mind_map = MindMap.new("Números", map_type="conceptual")
        first = mind_map.add_concept("1")
        second = mind_map.add_concept("2")
        third = mind_map.add_concept("3")

        choices = mind_map.concept_choice_labels()

        self.assertEqual(list(choices)[1:], ["1", "2", "3"])
        self.assertEqual(choices["1"], first.id)
        self.assertEqual(choices["2"], second.id)
        self.assertEqual(choices["3"], third.id)
        self.assertNotIn(first.id[:4], " ".join(choices))

    def test_relationship_selector_numbers_only_repeated_concept_text(self) -> None:
        mind_map = MindMap.new(map_type="conceptual")
        first = mind_map.add_concept("Novo conceito")
        second = mind_map.add_concept("Novo conceito")

        choices = mind_map.concept_choice_labels()

        self.assertEqual(choices["Novo conceito (1)"], first.id)
        self.assertEqual(choices["Novo conceito (2)"], second.id)

    def test_thinktrellis_opens_legacy_nexmind_documents(self) -> None:
        original = MindMap.new("Compatível")
        payload = original.to_dict()
        payload["app"] = "NexMind"

        reopened, _theme = MindMap.from_dict(payload)

        self.assertEqual(reopened.title, "Compatível")

    def test_old_map_without_type_remains_mental(self) -> None:
        original = MindMap.new("Compatível")
        payload = original.to_dict()
        payload.pop("map_type")
        payload.pop("relationships")

        reopened, _theme = MindMap.from_dict(payload)

        self.assertEqual(reopened.map_type, "mind")
        self.assertEqual(reopened.relationships, {})


if __name__ == "__main__":
    unittest.main()
