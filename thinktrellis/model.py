from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from . import APP_NAME, APP_VERSION, LEGACY_APP_NAMES, MAP_FORMAT_VERSION


DEFAULT_NODE_COLORS = (
    "#3B82F6",  # azul
    "#8B5CF6",  # violeta
    "#EC4899",  # rosa
    "#F97316",  # laranja
    "#10B981",  # verde
    "#14B8A6",  # turquesa
    "#EAB308",  # amarelo
    "#64748B",  # cinza azulado
)


class MapValidationError(ValueError):
    """Raised when a map document cannot be opened safely."""


def _new_id() -> str:
    return uuid4().hex[:12]


@dataclass(slots=True)
class MindNode:
    id: str
    text: str
    x: float
    y: float
    color: str = DEFAULT_NODE_COLORS[0]
    note: str = ""
    parent_id: str | None = None
    collapsed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "MindNode":
        if not isinstance(value, dict):
            raise MapValidationError("Um item do mapa não possui formato válido.")

        try:
            node_id = str(value["id"])
            text = str(value.get("text", "Ideia sem nome")).strip() or "Ideia sem nome"
            x = float(value.get("x", 0))
            y = float(value.get("y", 0))
        except (KeyError, TypeError, ValueError) as error:
            raise MapValidationError("Um item do mapa está incompleto.") from error

        parent = value.get("parent_id")
        return cls(
            id=node_id,
            text=text,
            x=x,
            y=y,
            color=str(value.get("color", DEFAULT_NODE_COLORS[0])),
            note=str(value.get("note", "")),
            parent_id=str(parent) if parent else None,
            collapsed=bool(value.get("collapsed", False)),
        )


@dataclass(slots=True)
class ConceptRelationship:
    """A labelled connection used only by conceptual maps."""

    id: str
    source_id: str
    target_id: str
    label: str = "relaciona-se com"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "ConceptRelationship":
        if not isinstance(value, dict):
            raise MapValidationError("Uma relação conceitual não possui formato válido.")
        try:
            relation_id = str(value["id"])
            source_id = str(value["source_id"])
            target_id = str(value["target_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise MapValidationError("Uma relação conceitual está incompleta.") from error
        return cls(
            id=relation_id,
            source_id=source_id,
            target_id=target_id,
            label=str(value.get("label", "relaciona-se com")).strip() or "relaciona-se com",
        )


class MindMap:
    """Modelo independente da interface, salvo em JSON leve e legível."""

    horizontal_spacing = 280
    vertical_spacing = 108

    def __init__(
        self,
        title: str,
        root_id: str,
        nodes: dict[str, MindNode],
        map_type: str = "mind",
        relationships: dict[str, ConceptRelationship] | None = None,
    ):
        self.title = title.strip() or "Novo mapa"
        self.root_id = root_id
        self.nodes = nodes
        self.map_type = map_type
        self.relationships = relationships or {}
        self.validate()

    @classmethod
    def new(cls, title: str = "Novo mapa", map_type: str = "mind") -> "MindMap":
        if map_type not in ("mind", "conceptual"):
            raise MapValidationError("O tipo de mapa informado não é válido.")
        root = MindNode(
            id=_new_id(),
            text="Ideia principal" if map_type == "mind" else "Conceito central",
            x=0,
            y=0,
            color="#2563EB",
        )
        return cls(title=title, root_id=root.id, nodes={root.id: root}, map_type=map_type)

    @property
    def root(self) -> MindNode:
        return self.nodes[self.root_id]

    def validate(self) -> None:
        if self.map_type not in ("mind", "conceptual"):
            raise MapValidationError("O arquivo usa um tipo de mapa não reconhecido.")
        if not self.nodes:
            raise MapValidationError("O mapa não possui ideias.")
        if self.root_id not in self.nodes:
            raise MapValidationError("A ideia principal não foi encontrada.")
        if self.nodes[self.root_id].parent_id is not None:
            raise MapValidationError("A ideia principal não pode ter uma ideia acima dela.")

        for node in self.nodes.values():
            if node.id != self.root_id and node.parent_id is not None and node.parent_id not in self.nodes:
                raise MapValidationError("Uma ideia aponta para uma ideia pai inexistente.")
            if self.map_type == "mind" and node.id != self.root_id and node.parent_id is None:
                raise MapValidationError("Uma ideia do mapa mental não possui uma ideia acima dela.")
            cursor = node
            visited: set[str] = set()
            while cursor.parent_id:
                if cursor.id in visited:
                    raise MapValidationError("O mapa contém uma conexão circular.")
                visited.add(cursor.id)
                cursor = self.nodes[cursor.parent_id]

        for relationship in self.relationships.values():
            if relationship.source_id not in self.nodes or relationship.target_id not in self.nodes:
                raise MapValidationError("Uma relação conceitual aponta para um conceito inexistente.")
            if relationship.source_id == relationship.target_id:
                raise MapValidationError("Um conceito não pode apontar para ele mesmo.")

    def children(self, node_id: str) -> list[MindNode]:
        return [node for node in self.nodes.values() if node.parent_id == node_id]

    def descendants(self, node_id: str) -> list[MindNode]:
        result: list[MindNode] = []
        for child in self.children(node_id):
            result.append(child)
            result.extend(self.descendants(child.id))
        return result

    def visible_nodes(self) -> Iterable[MindNode]:
        for node in self.nodes.values():
            cursor = node
            visible = True
            while cursor.parent_id:
                parent = self.nodes[cursor.parent_id]
                if parent.collapsed:
                    visible = False
                    break
                cursor = parent
            if visible:
                yield node

    def add_child(self, parent_id: str, text: str = "Nova ideia") -> MindNode:
        if self.map_type != "mind":
            raise MapValidationError("Subideias são usadas apenas em mapas mentais.")
        parent = self.nodes[parent_id]
        siblings = self.children(parent_id)
        direction = 1 if parent_id == self.root_id and len(siblings) % 2 == 0 else (1 if parent.x >= 0 else -1)
        child = MindNode(
            id=_new_id(),
            text=text.strip() or "Nova ideia",
            x=parent.x + direction * self.horizontal_spacing,
            y=parent.y + (len(siblings) - 0.5) * self.vertical_spacing,
            color=DEFAULT_NODE_COLORS[(len(self.nodes) - 1) % len(DEFAULT_NODE_COLORS)],
            parent_id=parent_id,
        )
        self.nodes[child.id] = child
        return child

    def add_sibling(self, node_id: str, text: str = "Nova ideia") -> MindNode:
        if self.map_type != "mind":
            raise MapValidationError("Ideias no mesmo nível são usadas apenas em mapas mentais.")
        node = self.nodes[node_id]
        if node.parent_id is None:
            raise MapValidationError("A ideia principal não tem ideias no mesmo nível.")
        sibling = self.add_child(node.parent_id, text)
        sibling.color = node.color
        return sibling

    def add_concept(self, text: str = "Novo conceito") -> MindNode:
        if self.map_type != "conceptual":
            raise MapValidationError("Conceitos avulsos são usados apenas em mapas conceituais.")
        position = len(self.nodes) - 1
        columns = 3
        column = position % columns
        row = position // columns
        concept = MindNode(
            id=_new_id(),
            text=text.strip() or "Novo conceito",
            x=(column - 1) * 280,
            y=160 + row * 150,
            color=DEFAULT_NODE_COLORS[position % len(DEFAULT_NODE_COLORS)],
        )
        self.nodes[concept.id] = concept
        return concept

    def add_relationship(self, source_id: str, target_id: str, label: str = "relaciona-se com") -> ConceptRelationship:
        if self.map_type != "conceptual":
            raise MapValidationError("Relações nomeadas são usadas apenas em mapas conceituais.")
        if source_id not in self.nodes or target_id not in self.nodes:
            raise MapValidationError("Escolha dois conceitos existentes para conectar.")
        if source_id == target_id:
            raise MapValidationError("Escolha dois conceitos diferentes para conectar.")
        clean_label = label.strip() or "relaciona-se com"
        for relationship in self.relationships.values():
            if relationship.source_id == source_id and relationship.target_id == target_id:
                relationship.label = clean_label
                return relationship
        relationship = ConceptRelationship(
            id=_new_id(),
            source_id=source_id,
            target_id=target_id,
            label=clean_label,
        )
        self.relationships[relationship.id] = relationship
        return relationship

    def concept_choice_labels(self) -> dict[str, str]:
        """Cria nomes fáceis de reconhecer para a janela de ligar conceitos.

        Cada opção mostra o texto que a pessoa escreveu dentro da caixa. Quando
        há conceitos com o mesmo texto, a numeração é humana ("Novo conceito
        (1)", "Novo conceito (2)") e nunca expõe IDs técnicos.
        """
        nodes = list(self.nodes.values())
        base_labels = [" ".join(node.text.split()) or "Conceito sem nome" for node in nodes]
        totals = Counter(base_labels)
        occurrences: Counter[str] = Counter()
        choices: dict[str, str] = {}

        for node, base in zip(nodes, base_labels):
            occurrences[base] += 1
            label = base if totals[base] == 1 else f"{base} ({occurrences[base]})"
            # Evita colisão até se alguém escrever manualmente um texto igual
            # ao sufixo criado para uma caixa duplicada.
            candidate = label
            duplicate_number = 2
            while candidate in choices:
                candidate = f"{label} — {duplicate_number}"
                duplicate_number += 1
            choices[candidate] = node.id
        return choices

    def relationships_for(self, node_id: str) -> list[ConceptRelationship]:
        return [
            relationship
            for relationship in self.relationships.values()
            if relationship.source_id == node_id or relationship.target_id == node_id
        ]

    def delete_branch(self, node_id: str) -> list[str]:
        if node_id == self.root_id:
            raise MapValidationError("A ideia principal não pode ser apagada.")
        removed = [node_id, *(node.id for node in self.descendants(node_id))]
        for item_id in removed:
            self.nodes.pop(item_id, None)
        for relationship_id, relationship in list(self.relationships.items()):
            if relationship.source_id in removed or relationship.target_id in removed:
                self.relationships.pop(relationship_id)
        return removed

    def arrange(self) -> None:
        """Organiza as ramificações em volta da ideia principal.

        A organização é deliberadamente previsível: ramos alternam esquerda e
        direita, e cada ramo conserva os seus filhos do mesmo lado.
        """

        if self.map_type == "conceptual":
            self._arrange_conceptual()
            return

        root = self.root
        root.x, root.y = 0, 0
        direct = self.children(root.id)
        left = direct[1::2]
        right = direct[0::2]

        self._arrange_side(left, -1)
        self._arrange_side(right, 1)

    def _arrange_conceptual(self) -> None:
        root = self.root
        root.x, root.y = 0, -180
        concepts = [node for node in self.nodes.values() if node.id != root.id]
        if not concepts:
            return
        columns = min(3, max(1, len(concepts)))
        spacing_x, spacing_y = 290, 155
        for index, concept in enumerate(concepts):
            column = index % columns
            row = index // columns
            concept.x = (column - (columns - 1) / 2) * spacing_x
            concept.y = 60 + row * spacing_y

    def _arrange_side(self, branch_roots: list[MindNode], direction: int) -> None:
        if not branch_roots:
            return

        leaves = sum(self._leaf_count(node.id) for node in branch_roots)
        cursor = -((leaves - 1) * self.vertical_spacing) / 2
        for node in branch_roots:
            cursor = self._arrange_branch(node.id, depth=1, direction=direction, cursor=cursor)

    def _leaf_count(self, node_id: str) -> int:
        node = self.nodes[node_id]
        children = self.children(node_id)
        if node.collapsed or not children:
            return 1
        return sum(self._leaf_count(child.id) for child in children)

    def _arrange_branch(self, node_id: str, depth: int, direction: int, cursor: float) -> float:
        node = self.nodes[node_id]
        node.x = direction * depth * self.horizontal_spacing
        children = [] if node.collapsed else self.children(node_id)
        if not children:
            node.y = cursor
            return cursor + self.vertical_spacing

        child_y: list[float] = []
        for child in children:
            cursor = self._arrange_branch(child.id, depth + 1, direction, cursor)
            child_y.append(child.y)
        node.y = sum(child_y) / len(child_y)
        return cursor

    def to_dict(self, theme: str = "dark") -> dict:
        return {
            "app": APP_NAME,
            "app_version": APP_VERSION,
            "format_version": MAP_FORMAT_VERSION,
            "title": self.title,
            "theme": theme,
            "map_type": self.map_type,
            "root_id": self.root_id,
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "relationships": [relationship.to_dict() for relationship in self.relationships.values()],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> tuple["MindMap", str]:
        if not isinstance(payload, dict):
            raise MapValidationError("O arquivo não parece ser um mapa ThinkTrellis.")
        if payload.get("app") not in (None, APP_NAME, *LEGACY_APP_NAMES):
            raise MapValidationError("Este arquivo foi criado por outro aplicativo.")

        raw_nodes = payload.get("nodes")
        if not isinstance(raw_nodes, list):
            raise MapValidationError("O arquivo não contém uma lista de ideias válida.")
        nodes = {node.id: node for node in (MindNode.from_dict(item) for item in raw_nodes)}
        if len(nodes) != len(raw_nodes):
            raise MapValidationError("O arquivo contém IDs de ideias duplicados.")

        raw_relationships = payload.get("relationships", [])
        if not isinstance(raw_relationships, list):
            raise MapValidationError("O arquivo não contém uma lista de relações válida.")
        relationships = {
            relationship.id: relationship
            for relationship in (ConceptRelationship.from_dict(item) for item in raw_relationships)
        }
        if len(relationships) != len(raw_relationships):
            raise MapValidationError("O arquivo contém IDs de relações duplicados.")

        mind_map = cls(
            title=str(payload.get("title", "Mapa sem título")),
            root_id=str(payload.get("root_id", "")),
            nodes=nodes,
            map_type=str(payload.get("map_type", "mind")),
            relationships=relationships,
        )
        theme = str(payload.get("theme", "dark"))
        return mind_map, theme

    def save(self, path: str | Path, theme: str = "dark") -> None:
        target = Path(path)
        target.write_text(
            json.dumps(self.to_dict(theme), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> tuple["MindMap", str]:
        source = Path(path)
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise MapValidationError("O arquivo está corrompido ou não está em JSON.") from error
        return cls.from_dict(payload)
