from __future__ import annotations

import html
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
import traceback
from typing import Literal

from . import (
    APP_NAME,
    APP_VERSION,
    COPYRIGHT_NOTICE,
    DEVELOPER_CREDIT,
    LEGACY_MAP_EXTENSIONS,
    MAP_FILE_EXTENSION,
)
from .colors import readable_text_color, safe_hex_color as _safe_hex_color
from .model import DEFAULT_NODE_COLORS, MapValidationError, MindMap, MindNode

try:  # O executável inclui esta biblioteca; o gerador interno mantém o app independente dela.
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen.canvas import Canvas as ReportLabCanvas
except ImportError:  # pragma: no cover - depende da instalação usada para executar o app.
    HexColor = None
    ReportLabCanvas = None


# Visual moderno e limpo: fundos suaves, cantos arredondados, linhas finas e
# um único azul de destaque. Os dois temas usam exatamente as mesmas chaves.
THEMES = {
    "light": {
        "name": "Claro",
        "bg": "#F7F9FC",
        "surface": "#F7F9FC",
        "field": "#FFFFFF",
        "canvas": "#FFFFFF",
        "canvas_grid": "#EDF1F7",
        "border": "#D3DBE8",
        "shadow": "#E1E7F1",
        "text": "#1B2438",
        "muted": "#5B6880",
        "disabled": "#9AA5B8",
        "primary": "#2563EB",
        "primary_hover": "#1D4ED8",
        "primary_soft": "#E3ECFE",
        "primary_text": "#FFFFFF",
        "danger": "#C0343F",
        "connection": "#9AABC4",
    },
    "dark": {
        "name": "Escuro",
        "bg": "#141C2E",
        "surface": "#141C2E",
        "field": "#0F1626",
        "canvas": "#0E1524",
        "canvas_grid": "#18223A",
        "border": "#2B3852",
        "shadow": "#0A101D",
        "text": "#F3F6FC",
        "muted": "#9FB0CB",
        "disabled": "#5F6E88",
        "primary": "#2563EB",
        "primary_hover": "#3B82F6",
        "primary_soft": "#1E3A6E",
        "primary_text": "#FFFFFF",
        "danger": "#FF8A93",
        "connection": "#566B92",
    },
}

DEFAULT_THEME = "light"


PDF_PAGE_WIDTH = 841.89  # A4 na horizontal, em pontos PDF.
PDF_PAGE_HEIGHT = 595.28


def _pdf_color(color: str) -> tuple[float, float, float]:
    """Converte uma cor hexadecimal do mapa para componentes de PDF."""
    raw = color.lstrip("#")
    if len(raw) != 6:
        return 0.12, 0.16, 0.23
    try:
        return tuple(int(raw[index:index + 2], 16) / 255 for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return 0.12, 0.16, 0.23


def _pdf_display_text(value: str) -> str:
    """Normaliza texto para as fontes padrão usadas pelo PDF."""
    clean = " ".join(str(value).replace("\x00", "").split())
    return clean.encode("cp1252", "replace").decode("cp1252")


def _pdf_literal(value: str) -> str:
    """Deixa texto seguro para uma string literal do formato PDF básico."""
    clean = _pdf_display_text(value)
    return clean.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_text_width(value: str, size: float) -> float:
    """Estimativa usada apenas pelo exportador sem dependências externas."""
    return len(_pdf_literal(value)) * size * 0.54


def wrap_label(text: str, width: int = 21) -> list[str]:
    """Wrap map labels without splitting a word unless unavoidable."""
    parts: list[str] = []
    for paragraph in text.splitlines() or [""]:
        wrapped = textwrap.wrap(
            paragraph,
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
        )
        parts.extend(wrapped or [""])
    return parts[:4]


class MindMapCanvas(tk.Canvas):
    """Canvas com zoom, arraste, conexões e seleção de ideias."""

    def __init__(self, master: tk.Misc, app: "ThinkTrellisApp", **kwargs):
        super().__init__(master, highlightthickness=0, borderwidth=0, takefocus=True, **kwargs)
        self.app = app
        self.scale = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._drag_node_id: str | None = None
        self._drag_start: tuple[float, float] | None = None
        self._drag_history_snapshot: dict | None = None
        self._pan_start: tuple[float, float] | None = None
        self._moved = False
        self._node_boxes: dict[str, tuple[float, float, float, float]] = {}
        self._hovered: str | None = None

        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", self._on_double_click)
        self.bind("<ButtonPress-3>", self._on_context_menu)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _event: self._set_hover(None))
        self.bind("<MouseWheel>", self._on_mouse_wheel)
        self.bind("<Button-4>", lambda event: self._on_linux_wheel(event, 1))
        self.bind("<Button-5>", lambda event: self._on_linux_wheel(event, -1))
        self.bind("<Configure>", lambda _event: self.redraw())

    @property
    def palette(self) -> dict[str, str]:
        return self.app.palette

    def reset_view(self) -> None:
        self.scale = 1.0
        self.pan_x = self.winfo_width() / 2
        self.pan_y = self.winfo_height() / 2
        self.redraw()

    def center_on_root(self) -> None:
        root = self.app.map.root
        self.pan_x = self.winfo_width() / 2 - root.x * self.scale
        self.pan_y = self.winfo_height() / 2 - root.y * self.scale
        self.redraw()

    def fit_map(self) -> None:
        nodes = list(self.app.map.visible_nodes())
        if not nodes:
            return
        boxes = [self.world_box(node) for node in nodes]
        min_x = min(box[0] for box in boxes) - 70
        min_y = min(box[1] for box in boxes) - 70
        max_x = max(box[2] for box in boxes) + 70
        max_y = max(box[3] for box in boxes) + 70
        width = max(1, max_x - min_x)
        height = max(1, max_y - min_y)
        canvas_w = max(300, self.winfo_width())
        canvas_h = max(250, self.winfo_height())
        self.scale = max(0.38, min(1.55, min((canvas_w - 50) / width, (canvas_h - 50) / height)))
        self.pan_x = canvas_w / 2 - ((min_x + max_x) / 2) * self.scale
        self.pan_y = canvas_h / 2 - ((min_y + max_y) / 2) * self.scale
        self.redraw()

    def zoom_by(self, factor: float, focus_x: float | None = None, focus_y: float | None = None) -> None:
        focus_x = self.winfo_width() / 2 if focus_x is None else focus_x
        focus_y = self.winfo_height() / 2 if focus_y is None else focus_y
        world_x, world_y = self.to_world(focus_x, focus_y)
        self.scale = max(0.35, min(2.4, self.scale * factor))
        self.pan_x = focus_x - world_x * self.scale
        self.pan_y = focus_y - world_y * self.scale
        self.redraw()

    def to_screen(self, x: float, y: float) -> tuple[float, float]:
        return x * self.scale + self.pan_x, y * self.scale + self.pan_y

    def to_world(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.pan_x) / self.scale, (y - self.pan_y) / self.scale

    def node_dimensions(self, node: MindNode) -> tuple[float, float]:
        lines = wrap_label(node.text)
        width = 220 if node.id == self.app.map.root_id else 190
        height = max(66, min(120, 38 + len(lines) * 18))
        return width, height

    def world_box(self, node: MindNode) -> tuple[float, float, float, float]:
        width, height = self.node_dimensions(node)
        return (
            node.x - width / 2,
            node.y - height / 2,
            node.x + width / 2,
            node.y + height / 2,
        )

    def screen_box(self, node: MindNode) -> tuple[float, float, float, float]:
        left, top, right, bottom = self.world_box(node)
        x1, y1 = self.to_screen(left, top)
        x2, y2 = self.to_screen(right, bottom)
        return x1, y1, x2, y2

    def redraw(self) -> None:
        if not self.winfo_exists():
            return
        self.delete("all")
        self.configure(bg=self.palette["canvas"])
        self._node_boxes.clear()
        self._draw_grid()

        visible = list(self.app.map.visible_nodes())
        visible_ids = {node.id for node in visible}
        if self.app.map.map_type == "mind":
            for node in visible:
                if node.parent_id and node.parent_id in visible_ids:
                    self._draw_connection(self.app.map.nodes[node.parent_id], node)
        else:
            for relationship in self.app.map.relationships.values():
                if relationship.source_id in visible_ids and relationship.target_id in visible_ids:
                    self._draw_relationship(
                        self.app.map.nodes[relationship.source_id],
                        self.app.map.nodes[relationship.target_id],
                        relationship.label,
                    )

        for node in visible:
            self._draw_node(node)

        if len(self.app.map.nodes) == 1:
            self.create_text(
                self.winfo_width() / 2,
                min(self.winfo_height() - 44, self.pan_y + 92),
                text=(
                    "Selecione “Ideia principal” e use + Subideia para começar."
                    if self.app.map.map_type == "mind"
                    else "Selecione “Conceito central” e use + Conceito para começar."
                ),
                fill=self.palette["muted"],
                font=self.app.ui_font(10),
                tags=("hint",),
            )

        self.app.refresh_zoom_label()

    def _draw_grid(self) -> None:
        spacing = 48 * self.scale
        if spacing < 22:
            return
        width, height = self.winfo_width(), self.winfo_height()
        start_x = self.pan_x % spacing
        start_y = self.pan_y % spacing
        for x in range(int(start_x), width, int(spacing)):
            self.create_line(x, 0, x, height, fill=self.palette["canvas_grid"], width=1)
        for y in range(int(start_y), height, int(spacing)):
            self.create_line(0, y, width, y, fill=self.palette["canvas_grid"], width=1)

    def _draw_connection(self, parent: MindNode, child: MindNode) -> None:
        start, end = self._connection_endpoints(parent, child)
        horizontal = abs(child.x - parent.x) >= abs(child.y - parent.y)
        if horizontal:
            middle = (start[0] + end[0]) / 2
            points = (start[0], start[1], middle, start[1], middle, end[1], end[0], end[1])
        else:
            middle = (start[1] + end[1]) / 2
            points = (start[0], start[1], start[0], middle, end[0], middle, end[0], end[1])
        # Curva suave que sai de uma caixa e chega na outra.
        self.create_line(
            *points,
            fill=self.palette["connection"],
            width=max(1, int(2 * self.scale)),
            smooth=True,
            capstyle="round",
            tags=("connection",),
        )

    def _connection_endpoints(self, source: MindNode, target: MindNode) -> tuple[tuple[float, float], tuple[float, float]]:
        source_box = self.screen_box(source)
        target_box = self.screen_box(target)
        dx, dy = target.x - source.x, target.y - source.y
        if abs(dx) >= abs(dy):
            if dx >= 0:
                return (
                    (source_box[2], (source_box[1] + source_box[3]) / 2),
                    (target_box[0], (target_box[1] + target_box[3]) / 2),
                )
            return (
                (source_box[0], (source_box[1] + source_box[3]) / 2),
                (target_box[2], (target_box[1] + target_box[3]) / 2),
            )
        if dy >= 0:
            return (
                ((source_box[0] + source_box[2]) / 2, source_box[3]),
                ((target_box[0] + target_box[2]) / 2, target_box[1]),
            )
        return (
            ((source_box[0] + source_box[2]) / 2, source_box[1]),
            ((target_box[0] + target_box[2]) / 2, target_box[3]),
        )

    def _draw_relationship(self, source: MindNode, target: MindNode, label: str) -> None:
        p = self.palette
        start, end = self._connection_endpoints(source, target)
        midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
        self.create_line(
            start[0], start[1], end[0], end[1],
            fill=p["connection"],
            width=max(1, int(2 * self.scale)),
            arrow=tk.LAST,
            arrowshape=(10, 12, 4),
            tags=("relationship",),
        )
        # A frase da relação fica sobre a seta, dentro de uma etiqueta simples.
        text_id = self.create_text(
            midpoint[0],
            midpoint[1],
            text=label,
            fill=p["text"],
            font=self.app.ui_font(max(8, int(9 * self.scale)), "bold"),
            tags=("relationship",),
        )
        box = self.bbox(text_id)
        if box:
            backdrop = self.create_rectangle(
                box[0] - 4, box[1] - 2, box[2] + 4, box[3] + 2,
                fill=p["canvas"],
                outline=p["border"],
                tags=("relationship",),
            )
            self.tag_lower(backdrop, text_id)

    def _round_rect(self, x1: float, y1: float, x2: float, y2: float, radius: float, **options) -> int:
        """Retângulo de cantos arredondados (polígono suavizado)."""
        radius = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
        points = (
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        )
        return self.create_polygon(points, smooth=True, **options)

    def _draw_node(self, node: MindNode) -> None:
        p = self.palette
        x1, y1, x2, y2 = self.screen_box(node)
        self._node_boxes[node.id] = (x1, y1, x2, y2)
        selected = node.id == self.app.selected_id
        hovered = node.id == self._hovered
        tags = ("node", node.id)
        text_color = readable_text_color(node.color)
        radius = max(5.0, 12.0 * self.scale)

        self._round_rect(x1, y1 + 3, x2, y2 + 3, radius, fill=p["shadow"], outline="", tags=tags)
        if selected:
            self._round_rect(x1 - 4, y1 - 4, x2 + 4, y2 + 4, radius + 3, fill="", outline=p["primary"], width=3, tags=tags)
        elif hovered:
            self._round_rect(x1 - 3, y1 - 3, x2 + 3, y2 + 3, radius + 2, fill="", outline=p["connection"], width=2, tags=tags)
        self._round_rect(x1, y1, x2, y2, radius, fill=node.color, outline=p["border"], tags=tags)

        text_size = max(8, min(14, int(10 * self.scale)))
        lines = wrap_label(node.text)
        note_dot = " •" if node.note.strip() else ""
        label = "\n".join(lines) + note_dot
        self.create_text(
            (x1 + x2) / 2,
            (y1 + y2) / 2,
            text=label,
            width=max(68, x2 - x1 - 28),
            fill=text_color,
            justify="center",
            font=self.app.ui_font(text_size, "bold"),
            tags=tags,
        )
        if node.collapsed and self.app.map.children(node.id):
            # Bolinha com "+" indica que há subideias recolhidas.
            size = max(14.0, 16.0 * self.scale)
            bx2, by1 = x2 - 6, y1 + 6
            bx1, by2 = bx2 - size, by1 + size
            self.create_oval(bx1, by1, bx2, by2, fill="#FFFFFF", outline=p["border"], tags=tags)
            self.create_text((bx1 + bx2) / 2, (by1 + by2) / 2, text="+", fill="#1B2438", font=self.app.ui_font(9, "bold"), tags=tags)

    def _hit_test(self, x: float, y: float) -> str | None:
        for node_id, (x1, y1, x2, y2) in reversed(list(self._node_boxes.items())):
            if x1 <= x <= x2 and y1 <= y <= y2:
                return node_id
        return None

    def _on_press(self, event: tk.Event) -> None:
        self.focus_set()
        node_id = self._hit_test(event.x, event.y)
        self._moved = False
        if node_id:
            self.app.select_node(node_id)
            self._drag_node_id = node_id
            self._drag_start = (event.x, event.y)
            self._drag_history_snapshot = self.app._history_snapshot()
            self._pan_start = None
        else:
            self._drag_node_id = None
            self._drag_start = None
            self._drag_history_snapshot = None
            self._pan_start = (event.x, event.y)

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag_node_id and self._drag_start:
            dx = (event.x - self._drag_start[0]) / self.scale
            dy = (event.y - self._drag_start[1]) / self.scale
            if abs(dx) > 0.4 or abs(dy) > 0.4:
                node = self.app.map.nodes[self._drag_node_id]
                node.x += dx
                node.y += dy
                self._drag_start = (event.x, event.y)
                self._moved = True
                self.redraw()
        elif self._pan_start:
            dx = event.x - self._pan_start[0]
            dy = event.y - self._pan_start[1]
            self.pan_x += dx
            self.pan_y += dy
            self._pan_start = (event.x, event.y)
            self.redraw()

    def _on_release(self, _event: tk.Event) -> None:
        if self._moved:
            self.app._remember_undo(self._drag_history_snapshot)
            self.app.mark_changed("Ideia movida — alterações não salvas")
        self._drag_node_id = None
        self._drag_start = None
        self._drag_history_snapshot = None
        self._pan_start = None

    def _on_double_click(self, event: tk.Event) -> None:
        node_id = self._hit_test(event.x, event.y)
        if node_id:
            self.app.select_node(node_id)
            self.app.focus_node_name()

    def _on_motion(self, event: tk.Event) -> None:
        self._set_hover(self._hit_test(event.x, event.y))

    def _set_hover(self, node_id: str | None) -> None:
        if node_id != self._hovered:
            self._hovered = node_id
            self.configure(cursor="hand2" if node_id else "")
            self.redraw()

    def _on_context_menu(self, event: tk.Event) -> None:
        node_id = self._hit_test(event.x, event.y)
        if not node_id:
            return
        self.app.select_node(node_id)
        menu = tk.Menu(self, tearoff=False)
        self.app.configure_menu(menu)
        if self.app.map.map_type == "mind":
            menu.add_command(label="Adicionar subideia", command=self.app.add_child)
            menu.add_command(label="Adicionar ideia no mesmo nível", command=self.app.add_sibling)
        else:
            menu.add_command(label="Adicionar conceito", command=self.app.add_concept)
            menu.add_command(label="Conectar a outro conceito", command=self.app.open_relationship_dialog)
        menu.add_separator()
        menu.add_command(label="Editar ideia", command=self.app.focus_node_name)
        if self.app.map.map_type == "mind":
            menu.add_command(label="Recolher / expandir", command=self.app.toggle_collapsed)
        menu.add_separator()
        menu.add_command(label="Apagar ideia", command=self.app.delete_selected)
        menu.tk_popup(event.x_root, event.y_root)

    def _on_mouse_wheel(self, event: tk.Event) -> None:
        if event.state & 0x0004:  # Ctrl: zoom
            self.zoom_by(1.10 if event.delta > 0 else 0.90, event.x, event.y)
        else:
            self.pan_y += event.delta / 3
            self.redraw()

    def _on_linux_wheel(self, event: tk.Event, direction: int) -> None:
        if event.state & 0x0004:
            self.zoom_by(1.10 if direction > 0 else 0.90, event.x, event.y)
        else:
            self.pan_y += direction * 26
            self.redraw()


class ThinkTrellisApp:
    """Editor offline de mapas mentais e conceituais, voltado a iniciantes."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.font_family = self._pick_font_family()
        self._apply_named_fonts()
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self._place_window()
        self._app_logo_image: tk.PhotoImage | None = None
        self._header_logo_image: tk.PhotoImage | None = None
        self._set_window_icon()

        self.data_dir = self._data_directory()
        self.preferences_path = self.data_dir / "preferences.json"
        self.autosave_path = self.data_dir / f"recovery{MAP_FILE_EXTENSION}"
        self.theme_name = self._read_preference("theme", DEFAULT_THEME)
        if self.theme_name not in THEMES:
            self.theme_name = DEFAULT_THEME
        self.palette = THEMES[self.theme_name]

        self.map = MindMap.new()
        self.file_path: Path | None = None
        self.selected_id = self.map.root_id
        self.dirty = False
        self._autosave_after: str | None = None
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._syncing_tree = False
        self._recovery_loaded = False
        self._menus: list[tk.Menu] = []
        self._swatches: list[tuple[tk.Button, str]] = []
        self._groups: list[tk.LabelFrame] = []
        self._bevels: list[tk.Frame] = []
        self._help_windows: list[tk.Toplevel] = []
        self._relationship_dialog: tk.Toplevel | None = None

        self.title_var = tk.StringVar(value=self.map.title)
        self.node_name_var = tk.StringVar()
        self.parent_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Pronto para criar seu mapa.")
        self.zoom_var = tk.StringVar(value="100%")

        self._configure_styles()
        self._build_menu()
        self._build_layout()
        self._bind_shortcuts()
        self.apply_theme(self.theme_name, persist=False)
        self._update_map_type_ui(refresh=False)
        self._update_history_controls()
        self.select_node(self.selected_id, focus_canvas=False)
        self.root.after(80, self.canvas.center_on_root)
        self.root.after(120, self._offer_recovery)
        self.root.after(250, self._offer_map_type_choice)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------
    # Fontes e janela
    # ------------------------------------------------------------------
    def _pick_font_family(self) -> str:
        """Escolhe a primeira fonte moderna e limpa que existir no sistema."""
        try:
            installed = {name.casefold(): name for name in tkfont.families(self.root)}
        except tk.TclError:
            return "Helvetica"
        for candidate in ("Segoe UI", "Inter", "Roboto", "Noto Sans", "Ubuntu", "Cantarell", "DejaVu Sans", "Liberation Sans", "Arial", "Helvetica"):
            if candidate.casefold() in installed:
                return installed[candidate.casefold()]
        return "Helvetica"

    def _apply_named_fonts(self) -> None:
        """Faz menus, listas e caixas de mensagem usarem a mesma fonte do app."""
        for name in (
            "TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
            "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont",
        ):
            try:
                tkfont.nametofont(name, root=self.root).configure(family=self.font_family, size=9)
            except tk.TclError:
                pass

    def ui_font(self, size: int = 9, weight: str = "normal") -> tuple[str, int, str]:
        return (self.font_family, size, weight)

    def _place_window(self) -> None:
        """Abre a janela centralizada, sem passar do tamanho da tela."""
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(1280, max(960, screen_width - 60))
        height = min(800, max(600, screen_height - 130))
        left = max(0, (screen_width - width) // 2)
        top = max(0, (screen_height - height) // 3)
        self.root.geometry(f"{width}x{height}+{left}+{top}")
        self.root.minsize(min(960, screen_width), min(600, screen_height))

    @staticmethod
    def _alive(widget: tk.Misc) -> bool:
        try:
            return bool(widget.winfo_exists())
        except tk.TclError:
            return False

    def _grab_when_visible(self, window: tk.Toplevel, attempts: int = 20) -> None:
        """Deixa a janela modal assim que ela aparece (no Linux o grab falha antes disso)."""
        try:
            window.grab_set()
        except tk.TclError:
            if attempts > 0 and self._alive(window):
                window.after(50, lambda: self._grab_when_visible(window, attempts - 1))

    def _data_directory(self) -> Path:
        # THINKTRELLIS_DATA_DIR permite testar sem mexer nos dados da pessoa.
        override = os.environ.get("THINKTRELLIS_DATA_DIR")
        base = Path(override) if override else Path.home() / ".thinktrellis"
        base.mkdir(parents=True, exist_ok=True)
        return base

    @staticmethod
    def _asset_path(filename: str) -> Path:
        """Encontra arquivos tanto no código quanto no executável PyInstaller."""
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        return base / "assets" / filename

    def _set_window_icon(self) -> None:
        # Versões já reduzidas da logo ficam mais nítidas que encolher a
        # imagem grande dentro do Tk.
        try:
            self._header_logo_image = tk.PhotoImage(file=str(self._asset_path("thinktrellis-logo-32.png")))
            self._app_logo_image = tk.PhotoImage(file=str(self._asset_path("thinktrellis-logo-64.png")))
            self.root.iconphoto(True, self._app_logo_image, self._header_logo_image)
        except tk.TclError:
            self._app_logo_image = None
            self._header_logo_image = None

        # O .ico dá ao Windows uma imagem nítida no atalho, no Explorer e na
        # barra de tarefas. Em outros sistemas o PNG acima continua valendo.
        try:
            self.root.iconbitmap(default=str(self._asset_path("thinktrellis.ico")))
        except tk.TclError:
            pass

    def _read_preference(self, key: str, default: str) -> str:
        try:
            return str(json.loads(self.preferences_path.read_text(encoding="utf-8")).get(key, default))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return default

    def _write_preferences(self) -> None:
        try:
            self.preferences_path.write_text(
                json.dumps({"theme": self.theme_name}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _configure_styles(self) -> None:
        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self.root, tearoff=False)
        self.root.configure(menu=menu_bar)
        self._menus.append(menu_bar)

        file_menu = self._new_menu(menu_bar)
        file_menu.add_command(label="Novo mapa", command=self.new_map, accelerator="Ctrl+N")
        file_menu.add_command(label="Abrir mapa…", command=self.open_map, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="Salvar", command=self.save, accelerator="Ctrl+S")
        file_menu.add_command(label="Salvar como…", command=self.save_as, accelerator="Ctrl+Shift+S")
        file_menu.add_separator()
        file_menu.add_command(label="Exportar como PDF…", command=self.export_pdf)
        file_menu.add_command(label="Exportar como SVG…", command=self.export_svg)
        file_menu.add_separator()
        file_menu.add_command(label="Sair", command=self.on_close)
        menu_bar.add_cascade(label="Arquivo", menu=file_menu)

        self.edit_menu = self._new_menu(menu_bar)
        self._populate_edit_menu()
        menu_bar.add_cascade(label="Editar", menu=self.edit_menu)

        view_menu = self._new_menu(menu_bar)
        view_menu.add_command(label="Aproximar", command=lambda: self.canvas.zoom_by(1.15), accelerator="Ctrl++")
        view_menu.add_command(label="Afastar", command=lambda: self.canvas.zoom_by(0.87), accelerator="Ctrl+-")
        view_menu.add_command(label="Ajustar mapa à tela", command=lambda: self.canvas.fit_map(), accelerator="Ctrl+0")
        view_menu.add_command(label="Centralizar ideia principal", command=lambda: self.canvas.center_on_root())
        view_menu.add_separator()
        theme_menu = self._new_menu(view_menu)
        self.theme_var = tk.StringVar(value=self.theme_name)
        theme_menu.add_radiobutton(label=THEMES["light"]["name"], value="light", variable=self.theme_var, command=lambda: self.apply_theme("light"))
        theme_menu.add_radiobutton(label=THEMES["dark"]["name"], value="dark", variable=self.theme_var, command=lambda: self.apply_theme("dark"))
        view_menu.add_cascade(label="Visual", menu=theme_menu)
        menu_bar.add_cascade(label="Visualizar", menu=view_menu)

        help_menu = self._new_menu(menu_bar)
        help_menu.add_command(label=f"Manual do {APP_NAME}", command=lambda: self.show_help("manual"), accelerator="F1")
        help_menu.add_command(label=f"Notas da versão {APP_VERSION}", command=lambda: self.show_help("notes"))
        help_menu.add_separator()
        help_menu.add_command(label=f"Sobre o {APP_NAME}", command=self.show_about)
        menu_bar.add_cascade(label="Ajuda", menu=help_menu)

    def _new_menu(self, master: tk.Misc) -> tk.Menu:
        menu = tk.Menu(master, tearoff=False)
        self._menus.append(menu)
        return menu

    def _populate_edit_menu(self) -> None:
        menu = self.edit_menu
        menu.delete(0, "end")
        menu.add_command(
            label="Desfazer",
            command=self.undo,
            accelerator="Ctrl+Z",
            state="normal" if self._undo_stack else "disabled",
        )
        menu.add_separator()
        if self.map.map_type == "mind":
            menu.add_command(label="Adicionar subideia", command=self.add_child, accelerator="Tab")
            menu.add_command(label="Adicionar ideia no mesmo nível", command=self.add_sibling, accelerator="Enter")
            menu.add_command(label="Recolher / expandir", command=self.toggle_collapsed)
        else:
            menu.add_command(label="Adicionar conceito", command=self.add_concept, accelerator="Tab")
            menu.add_command(label="Conectar conceitos…", command=self.open_relationship_dialog, accelerator="Enter")
        menu.add_command(label="Editar item", command=self.focus_node_name, accelerator="F2")
        menu.add_separator()
        menu.add_command(label="Organizar automaticamente", command=self.organize_map, accelerator="Ctrl+L")
        menu.add_command(label="Apagar item", command=self.delete_selected, accelerator="Delete")

    def configure_menu(self, menu: tk.Menu) -> None:
        p = self.palette
        menu.configure(
            background=p["surface"],
            foreground=p["text"],
            activebackground=p["primary"],
            activeforeground=p["primary_text"],
            disabledforeground=p["disabled"],
            borderwidth=1,
            relief="flat",
            font=self.ui_font(9),
        )

    def _group(self, master: tk.Misc, text: str) -> tk.LabelFrame:
        """Cartão com título e uma linha fina de contorno."""
        frame = tk.LabelFrame(master, text=text, bd=0, relief="flat", padx=12, pady=8, highlightthickness=1)
        self._groups.append(frame)
        self._style_group(frame)
        return frame

    def _style_group(self, frame: tk.LabelFrame) -> None:
        p = self.palette
        frame.configure(
            bg=p["surface"],
            fg=p["muted"],
            font=self.ui_font(9, "bold"),
            highlightbackground=p["border"],
            highlightcolor=p["border"],
        )

    def _bevel_frame(self, master: tk.Misc, relief: Literal["raised", "sunken"] = "sunken", border: int = 2) -> tk.Frame:
        """Moldura plana com contorno fino (os parâmetros antigos são ignorados)."""
        frame = tk.Frame(master, bd=0, relief="flat", highlightthickness=1, bg=self.palette["surface"])
        self._bevels.append(frame)
        return frame

    def _style_classic_widgets(self) -> None:
        p = self.palette
        self._groups = [frame for frame in self._groups if self._alive(frame)]
        self._bevels = [frame for frame in self._bevels if self._alive(frame)]
        for group in self._groups:
            self._style_group(group)
        for bevel in self._bevels:
            bevel.configure(bg=p["surface"], highlightbackground=p["border"], highlightcolor=p["border"])

    def _make_text(self, master: tk.Misc, **options) -> ScrolledText:
        """Campo de texto afundado, com a mesma barra de rolagem do resto do app."""
        text = ScrolledText(master, relief="flat", borderwidth=0, highlightthickness=1, **options)
        text.vbar.destroy()
        bar = ttk.Scrollbar(text.frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=bar.set)
        # str(ScrolledText) devolve o quadro externo, que ainda não foi
        # empacotado; por isso a ordem é refeita sem usar "before=".
        tk.Text.pack_forget(text)
        bar.pack(side="right", fill="y")
        tk.Text.pack(text, side="left", fill="both", expand=True)
        text.vbar = bar  # type: ignore[assignment]
        self._style_text(text)
        return text

    def _style_text(self, text: tk.Text) -> None:
        p = self.palette
        text.configure(
            bg=p["field"],
            fg=p["text"],
            insertbackground=p["text"],
            selectbackground=p["primary"],
            selectforeground=p["primary_text"],
            font=self.ui_font(9),
            padx=8,
            pady=6,
            highlightbackground=p["border"],
            highlightcolor=p["primary"],
        )

    def _refresh_swatches(self) -> None:
        """Mostra afundada a cor que a caixa selecionada está usando."""
        current = ""
        if self.selected_id in self.map.nodes:
            current = self.map.nodes[self.selected_id].color.upper()
        for button, color in self._swatches:
            chosen = color.upper() == current
            button.configure(
                highlightbackground=self.palette["primary"] if chosen else self.palette["border"],
                highlightthickness=3 if chosen else 1,
            )

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, style="App.TFrame", padding=0)
        outer.pack(fill="both", expand=True)

        # Barra superior: identidade, nome do mapa e as ações principais.
        header = ttk.Frame(outer, style="Header.TFrame", padding=(10, 6))
        header.pack(fill="x")
        if self._header_logo_image:
            logo = ttk.Label(header, image=self._header_logo_image)
        else:
            logo = ttk.Label(header, text="T", style="Logo.TLabel", anchor="center", width=2)
        logo.pack(side="left", padx=(0, 8))
        ttk.Label(header, text=APP_NAME, style="Brand.TLabel").pack(side="left")
        self.map_type_badge = ttk.Label(header, text="Mapa mental", style="Version.TLabel")
        self.map_type_badge.pack(side="left", padx=(10, 0))

        # Os botões são posicionados antes do título para que, numa janela
        # estreita, o campo do título encolha primeiro e nenhum botão suma.
        self.undo_button = ttk.Button(header, text="Desfazer", command=self.undo, style="Quiet.TButton")
        self.undo_button.pack(side="right", padx=(4, 0))
        self.organize_button = ttk.Button(header, text="Organizar", command=self.organize_map, style="Quiet.TButton")
        self.organize_button.pack(side="right", padx=(4, 0))
        ttk.Separator(header, orient="vertical").pack(side="right", fill="y", padx=8, pady=2)
        self.secondary_action_button = ttk.Button(header, text="+ Subideia", command=self.add_child, style="Primary.TButton")
        self.secondary_action_button.pack(side="right", padx=(4, 0))
        self.primary_action_button = ttk.Button(header, text="+ Outra ideia", command=self.add_sibling, style="Quiet.TButton")
        self.primary_action_button.pack(side="right", padx=(4, 0))

        self.map_title_entry = ttk.Entry(header, textvariable=self.title_var, style="Title.TEntry", width=28)
        self.map_title_entry.pack(side="left", fill="x", expand=True, padx=(14, 14))
        self.map_title_entry.bind("<FocusOut>", lambda _event: self.apply_map_title())
        self.map_title_entry.bind("<Return>", self._title_enter)

        ttk.Separator(outer, orient="horizontal").pack(fill="x")

        body = ttk.Frame(outer, style="App.TFrame", padding=(8, 8, 8, 6))
        body.pack(fill="both", expand=True)

        # Coluna da esquerda: como usar e estrutura do mapa.
        left = ttk.Frame(body, style="App.TFrame", width=236)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.guide_group = self._group(left, "Como usar")
        self.guide_group.pack(fill="x")
        self.intro_label = ttk.Label(self.guide_group, text="Um mapa de cada vez, sem complicação.", style="Hint.TLabel", wraplength=196)
        self.intro_label.pack(anchor="w", pady=(0, 4))
        self.step_one_title, self.step_one_detail = self._step(self.guide_group, "1", "Clique em uma caixa", "Ela fica marcada. À direita você pode mudar o nome e a cor.")
        self.step_two_title, self.step_two_detail = self._step(self.guide_group, "2", "Adicione uma subideia", "Com a caixa marcada, clique em + Subideia no alto.")
        self.step_three_title, self.step_three_detail = self._step(self.guide_group, "3", "Organize quando quiser", "Arraste as caixas ou clique em Organizar.")

        self.tree_group = self._group(left, "Estrutura do mapa")
        self.tree_group.pack(fill="both", expand=True, pady=(8, 0))
        tree_frame = ttk.Frame(self.tree_group, style="Panel.TFrame")
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, show="tree", selectmode="browse", style="Mind.Treeview")
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # Centro: o mapa, numa moldura afundada, com zoom logo acima.
        middle = ttk.Frame(body, style="App.TFrame")
        middle.pack(side="left", fill="both", expand=True, padx=8)
        canvas_toolbar = ttk.Frame(middle, style="App.TFrame")
        canvas_toolbar.pack(fill="x", pady=(0, 6))
        self.canvas_hint_label = ttk.Label(canvas_toolbar, text="Duplo clique edita • Arraste o fundo para navegar", style="Hint.TLabel")
        self.canvas_hint_label.pack(side="left")
        ttk.Button(canvas_toolbar, text="Ajustar", command=self.canvas_fit_safe, style="Quiet.TButton").pack(side="right")
        ttk.Button(canvas_toolbar, text="+", command=lambda: self.canvas.zoom_by(1.15), style="Zoom.TButton", width=3).pack(side="right", padx=(6, 0))
        ttk.Label(canvas_toolbar, textvariable=self.zoom_var, style="Zoom.TLabel", width=6, anchor="center").pack(side="right")
        ttk.Button(canvas_toolbar, text="-", command=lambda: self.canvas.zoom_by(0.87), style="Zoom.TButton", width=3).pack(side="right", padx=(0, 6))

        canvas_frame = self._bevel_frame(middle, relief="sunken", border=2)
        canvas_frame.pack(fill="both", expand=True)
        self.canvas = MindMapCanvas(canvas_frame, self, bg=self.palette["canvas"])
        self.canvas.pack(fill="both", expand=True)

        # Coluna da direita: detalhes da caixa selecionada.
        right = ttk.Frame(body, style="App.TFrame", width=272)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)
        self.details_group = self._group(right, "Detalhes da ideia")
        self.details_group.pack(fill="both", expand=True)
        details = self.details_group
        self.selected_badge = ttk.Label(details, text="", style="Selected.TLabel", wraplength=226)
        self.selected_badge.pack(anchor="w", fill="x", pady=(0, 8))
        ttk.Label(details, text="Título", style="Field.TLabel").pack(anchor="w")
        self.node_name_entry = ttk.Entry(details, textvariable=self.node_name_var, style="Inspector.TEntry")
        self.node_name_entry.pack(fill="x", pady=(3, 8))
        self.node_name_entry.bind("<Return>", self._inspector_enter)
        self.parent_field_label = ttk.Label(details, text="Conectada a", style="Field.TLabel")
        self.parent_field_label.pack(anchor="w")
        ttk.Label(details, textvariable=self.parent_var, style="Value.TLabel", wraplength=226).pack(anchor="w", pady=(3, 8))
        ttk.Label(details, text="Nota (opcional)", style="Field.TLabel").pack(anchor="w")
        self.note_text = self._make_text(details, height=5, wrap="word", undo=True)
        self.note_text.pack(fill="x", pady=(3, 8))
        ttk.Label(details, text="Cor", style="Field.TLabel").pack(anchor="w")
        colors = ttk.Frame(details, style="Panel.TFrame")
        colors.pack(fill="x", pady=(4, 10))
        for index, color in enumerate(DEFAULT_NODE_COLORS):
            button = tk.Button(
                colors,
                bg=color,
                activebackground=color,
                width=2,
                height=1,
                relief="flat",
                bd=0,
                highlightthickness=1,
                cursor="hand2",
                command=lambda current=color: self.set_selected_color(current),
            )
            button.grid(row=index // 4, column=index % 4, padx=2, pady=2, sticky="w")
            self._swatches.append((button, color))
        ttk.Button(details, text="Aplicar alterações", command=self.apply_inspector, style="Primary.TButton").pack(fill="x")
        self.collapse_button = ttk.Button(details, text="Recolher / expandir", command=self.toggle_collapsed, style="Quiet.TButton")
        self.collapse_button.pack(fill="x", pady=(6, 0))
        self.delete_button = ttk.Button(details, text="Apagar ideia", command=self.delete_selected, style="Danger.TButton")
        self.delete_button.pack(fill="x", pady=(6, 0))
        self.right_tip_label = ttk.Label(details, text="Não precisa decorar atalhos: use os botões no alto da tela.", style="Hint.TLabel", wraplength=226)
        self.right_tip_label.pack(anchor="w", side="bottom", pady=(10, 0))

        # Barra de status com dois campos afundados.
        status = ttk.Frame(outer, style="App.TFrame", padding=(8, 0, 8, 6))
        status.pack(fill="x")
        message_cell = self._bevel_frame(status, relief="sunken", border=1)
        message_cell.pack(side="left", fill="x", expand=True)
        ttk.Label(message_cell, textvariable=self.status_var, style="Status.TLabel", padding=(6, 2)).pack(side="left", fill="x", expand=True)
        file_cell = self._bevel_frame(status, relief="sunken", border=1)
        file_cell.pack(side="right", padx=(4, 0))
        ttk.Label(file_cell, text=f"Offline • Arquivos {MAP_FILE_EXTENSION}", style="Status.TLabel", padding=(8, 2)).pack()

    def _step(self, master: tk.Misc, number: str, title: str, detail: str) -> tuple[ttk.Label, ttk.Label]:
        step = ttk.Frame(master, style="Panel.TFrame")
        step.pack(fill="x", pady=4)
        ttk.Label(step, text=number, style="Step.TLabel", width=2, anchor="center").pack(side="left", anchor="n", padx=(0, 8))
        contents = ttk.Frame(step, style="Panel.TFrame")
        contents.pack(side="left", fill="x", expand=True)
        title_label = ttk.Label(contents, text=title, style="StepTitle.TLabel")
        title_label.pack(anchor="w")
        detail_label = ttk.Label(contents, text=detail, style="Hint.TLabel", wraplength=150)
        detail_label.pack(anchor="w", pady=(2, 0))
        return title_label, detail_label

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<Control-n>", lambda _event: self.new_map())
        self.root.bind_all("<Control-o>", lambda _event: self.open_map())
        self.root.bind_all("<Control-s>", lambda _event: self.save())
        self.root.bind_all("<Control-Shift-S>", lambda _event: self.save_as())
        self.root.bind_all("<Control-z>", self._shortcut_undo)
        self.root.bind_all("<Control-Z>", self._shortcut_undo)
        self.root.bind_all("<Control-l>", lambda _event: self.organize_map())
        self.root.bind_all("<Control-0>", lambda _event: self.canvas.fit_map())
        self.root.bind_all("<Control-plus>", lambda _event: self.canvas.zoom_by(1.15))
        self.root.bind_all("<Control-equal>", lambda _event: self.canvas.zoom_by(1.15))
        self.root.bind_all("<Control-minus>", lambda _event: self.canvas.zoom_by(0.87))
        self.root.bind_all("<F1>", lambda _event: self.show_help("manual"))
        self.root.bind_all("<F2>", lambda _event: self.focus_node_name() if self._canvas_shortcut_allowed() else None)
        self.root.bind_all("<Delete>", lambda _event: self.delete_selected() if self._canvas_shortcut_allowed() else None)
        # Tab é tratado diretamente no mapa e na árvore. Assim ele não é
        # engolido pela navegação padrão de foco do Tk/Windows.
        for widget in (self.canvas, self.tree):
            widget.bind("<Tab>", self._shortcut_child)
            widget.bind("<Return>", self._shortcut_sibling)
            widget.bind("<KP_Enter>", self._shortcut_sibling)

    def _canvas_shortcut_allowed(self) -> bool:
        focus = self.root.focus_get()
        if focus is not None and focus.winfo_toplevel() != self.root:
            return False
        return not isinstance(focus, (tk.Entry, tk.Text, ttk.Entry, ttk.Combobox))

    def _shortcut_undo(self, _event: tk.Event) -> str | None:
        # Enquanto a pessoa escreve, Ctrl+Z continua reservado ao próprio
        # campo de texto. No mapa ou na estrutura, ele desfaz a última ação.
        if self._canvas_shortcut_allowed():
            self.undo()
            return "break"
        return None

    def _shortcut_child(self, _event: tk.Event) -> str | None:
        if self._canvas_shortcut_allowed():
            self.add_child()
            return "break"
        return None

    def _shortcut_sibling(self, _event: tk.Event) -> str | None:
        if self._canvas_shortcut_allowed():
            if self.map.map_type == "conceptual":
                self.open_relationship_dialog()
            else:
                self.add_sibling()
            return "break"
        return None

    def _title_enter(self, _event: tk.Event) -> str:
        self.apply_map_title()
        self.canvas.focus_set()
        return "break"

    def _inspector_enter(self, _event: tk.Event) -> str:
        self.apply_inspector()
        self.canvas.focus_set()
        return "break"

    def apply_theme(self, theme_name: str, persist: bool = True) -> None:
        if theme_name not in THEMES:
            return
        self.theme_name = theme_name
        self.palette = THEMES[theme_name]
        if hasattr(self, "theme_var"):
            self.theme_var.set(theme_name)
        p = self.palette
        font = self.ui_font
        style = self.style
        self.root.configure(bg=p["bg"])

        # Base: fundo suave, texto escuro e contornos de uma linha só. As cores
        # "lightcolor/darkcolor" iguais à borda removem qualquer efeito de relevo.
        style.configure(
            ".",
            background=p["surface"],
            foreground=p["text"],
            font=font(9),
            bordercolor=p["border"],
            lightcolor=p["border"],
            darkcolor=p["border"],
            troughcolor=p["canvas_grid"],
            fieldbackground=p["field"],
            selectbackground=p["primary"],
            selectforeground=p["primary_text"],
            insertcolor=p["text"],
            focuscolor=p["surface"],
        )
        style.map(
            ".",
            background=[("disabled", p["surface"]), ("active", p["surface"])],
            foreground=[("disabled", p["disabled"])],
        )

        for frame_style in ("App.TFrame", "Header.TFrame", "Panel.TFrame", "Status.TFrame"):
            style.configure(frame_style, background=p["surface"])
        style.configure("TLabel", background=p["surface"], foreground=p["text"])
        style.configure("Brand.TLabel", font=font(13, "bold"))
        style.configure("Logo.TLabel", background=p["primary"], foreground=p["primary_text"], font=font(12, "bold"), padding=(6, 2))
        style.configure("Version.TLabel", background=p["primary_soft"], foreground=p["primary"] if self.theme_name == "light" else p["text"], font=font(9, "bold"), padding=(8, 2))
        style.configure("Hint.TLabel", foreground=p["muted"], font=font(9))
        style.configure("Field.TLabel", foreground=p["muted"], font=font(9, "bold"))
        style.configure("Value.TLabel", foreground=p["text"], font=font(9))
        style.configure("Selected.TLabel", background=p["primary_soft"], foreground=p["primary"] if self.theme_name == "light" else p["text"], font=font(9, "bold"), padding=(10, 6))
        style.configure("Step.TLabel", background=p["primary"], foreground=p["primary_text"], font=font(9, "bold"), padding=(4, 1))
        style.configure("StepTitle.TLabel", font=font(9, "bold"))
        style.configure("Status.TLabel", foreground=p["muted"], font=font(9))
        style.configure("Zoom.TLabel", foreground=p["text"], font=font(9, "bold"))
        style.configure("TSeparator", background=p["border"])

        # Botões planos: contorno fino e destaque suave ao passar o mouse.
        style.configure(
            "TButton",
            background=p["field"],
            foreground=p["text"],
            font=font(9),
            anchor="center",
            padding=(12, 5),
            borderwidth=1,
            relief="flat",
        )
        style.map(
            "TButton",
            background=[("disabled", p["surface"]), ("pressed", p["primary_soft"]), ("active", p["primary_soft"])],
            foreground=[("disabled", p["disabled"])],
            bordercolor=[("disabled", p["border"]), ("pressed", p["primary"]), ("active", p["primary"])],
            lightcolor=[("pressed", p["primary_soft"]), ("active", p["primary_soft"])],
            darkcolor=[("pressed", p["primary_soft"]), ("active", p["primary_soft"])],
        )
        # O botão principal é o único preenchido com a cor de destaque.
        style.configure(
            "Primary.TButton",
            background=p["primary"],
            foreground=p["primary_text"],
            lightcolor=p["primary"],
            darkcolor=p["primary"],
            bordercolor=p["primary"],
            font=font(9, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("disabled", p["border"]), ("pressed", p["primary_hover"]), ("active", p["primary_hover"])],
            foreground=[("disabled", p["disabled"])],
            lightcolor=[("pressed", p["primary_hover"]), ("active", p["primary_hover"])],
            darkcolor=[("pressed", p["primary_hover"]), ("active", p["primary_hover"])],
            bordercolor=[("pressed", p["primary_hover"]), ("active", p["primary_hover"])],
        )
        style.configure("Danger.TButton", foreground=p["danger"])
        style.configure("Zoom.TButton", padding=(4, 2))

        # Campos de texto com contorno fino que ganha a cor de destaque ao focar.
        for entry_style, entry_font in (
            ("TEntry", font(9)),
            ("Title.TEntry", font(10, "bold")),
            ("Inspector.TEntry", font(9)),
        ):
            style.configure(
                entry_style,
                fieldbackground=p["field"],
                foreground=p["text"],
                insertcolor=p["text"],
                bordercolor=p["border"],
                lightcolor=p["border"],
                darkcolor=p["border"],
                borderwidth=1,
                padding=(8, 5),
                font=entry_font,
            )
        style.map(
            "TEntry",
            bordercolor=[("focus", p["primary"])],
            lightcolor=[("focus", p["primary"])],
            darkcolor=[("focus", p["primary"])],
        )
        style.configure(
            "TCombobox",
            fieldbackground=p["field"],
            background=p["field"],
            foreground=p["text"],
            arrowcolor=p["muted"],
            bordercolor=p["border"],
            lightcolor=p["border"],
            darkcolor=p["border"],
            insertcolor=p["text"],
            padding=(8, 5),
            arrowsize=14,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", p["field"])],
            foreground=[("readonly", p["text"])],
            bordercolor=[("focus", p["primary"])],
        )
        self.root.option_add("*TCombobox*Listbox.background", p["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", p["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", p["primary"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", p["primary_text"])
        self.root.option_add("*TCombobox*Listbox.font", font(9))

        style.configure(
            "Treeview",
            background=p["field"],
            fieldbackground=p["field"],
            foreground=p["text"],
            bordercolor=p["border"],
            lightcolor=p["border"],
            darkcolor=p["border"],
            borderwidth=1,
            font=font(9),
            rowheight=26,
        )
        style.map(
            "Treeview",
            background=[("selected", p["primary"])],
            foreground=[("selected", p["primary_text"])],
        )
        style.configure(
            "TScrollbar",
            background=p["border"],
            troughcolor=p["surface"],
            bordercolor=p["surface"],
            lightcolor=p["border"],
            darkcolor=p["border"],
            arrowcolor=p["muted"],
            arrowsize=12,
            gripcount=0,
            borderwidth=0,
        )
        style.map(
            "TScrollbar",
            background=[("active", p["connection"]), ("pressed", p["connection"])],
        )
        style.configure(
            "TNotebook",
            background=p["surface"],
            bordercolor=p["border"],
            lightcolor=p["border"],
            darkcolor=p["border"],
            tabmargins=(0, 4, 0, 0),
        )
        style.configure(
            "TNotebook.Tab",
            background=p["surface"],
            foreground=p["muted"],
            bordercolor=p["border"],
            lightcolor=p["surface"],
            darkcolor=p["surface"],
            padding=(14, 6),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", p["field"]), ("active", p["primary_soft"])],
            foreground=[("selected", p["primary"] if self.theme_name == "light" else p["text"])],
        )

        self._style_classic_widgets()
        self._style_text(self.note_text)
        for window in self._help_windows:
            if self._alive(window):
                window.configure(bg=p["bg"])
        for menu in self._menus:
            self.configure_menu(menu)
        self._refresh_swatches()
        self.canvas.redraw()
        if persist:
            self._write_preferences()

    def toggle_theme(self) -> None:
        self.apply_theme("light" if self.theme_name == "dark" else "dark")

    def refresh_zoom_label(self) -> None:
        self.zoom_var.set(f"{round(self.canvas.scale * 100)}%")

    def canvas_fit_safe(self) -> None:
        self.canvas.fit_map()

    def _update_action_buttons(self) -> None:
        """Deixa os botões do alto coerentes com a caixa selecionada."""
        if not hasattr(self, "primary_action_button"):
            return
        if self.map.map_type == "conceptual":
            self.primary_action_button.configure(text="+ Conceito", command=self.add_concept, state="normal")
            self.secondary_action_button.configure(text="Conectar", command=self.open_relationship_dialog, state="normal")
            return

        can_add_sibling = self.selected_id in self.map.nodes and self.selected_id != self.map.root_id
        self.primary_action_button.configure(
            text="+ Outra ideia",
            command=self.add_sibling,
            state="normal" if can_add_sibling else "disabled",
        )
        self.secondary_action_button.configure(text="+ Subideia", command=self.add_child, state="normal")

    def _update_map_type_ui(self, refresh: bool = True) -> None:
        conceptual = self.map.map_type == "conceptual"
        if conceptual:
            self.map_type_badge.configure(text="Mapa conceitual")
            self.organize_button.configure(text="Organizar")
            self.intro_label.configure(text="Crie caixas e explique como uma se relaciona com a outra.")
            self.step_one_title.configure(text="Crie os conceitos")
            self.step_one_detail.configure(text="Clique em + Conceito para colocar as caixas que você precisa.")
            self.step_two_title.configure(text="Clique em Conectar")
            self.step_two_detail.configure(text="Escolha a caixa de onde a seta sai e a caixa para onde ela vai.")
            self.step_three_title.configure(text="Escreva a frase da seta")
            self.step_three_detail.configure(text="Exemplo: “1 depende de 2”. Depois clique em Criar ligação.")
            self.tree_group.configure(text="Conceitos do mapa")
            self.canvas_hint_label.configure(text="Clique para selecionar • Arraste as caixas • As setas mostram as relações")
            self.details_group.configure(text="Detalhes do conceito")
            self.parent_field_label.configure(text="Relações")
            self.collapse_button.configure(state="disabled")
            self.delete_button.configure(text="Apagar conceito")
            self.right_tip_label.configure(text="Use os botões + Conceito e Conectar no alto da tela. Os atalhos são opcionais.")
        else:
            self.map_type_badge.configure(text="Mapa mental")
            self.organize_button.configure(text="Organizar")
            self.intro_label.configure(text="Um mapa de cada vez, sem complicação.")
            self.step_one_title.configure(text="Escolha uma ideia")
            self.step_one_detail.configure(text="Clique na caixa que você quer desenvolver.")
            self.step_two_title.configure(text="Clique em + Subideia")
            self.step_two_detail.configure(text="Uma caixa nova aparece ligada à ideia escolhida.")
            self.step_three_title.configure(text="Dê um nome à nova caixa")
            self.step_three_detail.configure(text="Escreva à direita e clique em Aplicar alterações.")
            self.tree_group.configure(text="Estrutura do mapa")
            self.canvas_hint_label.configure(text="Clique para selecionar • Arraste caixas • Arraste o fundo para navegar")
            self.details_group.configure(text="Detalhes da ideia")
            self.parent_field_label.configure(text="Conectada a")
            self.collapse_button.configure(state="normal")
            self.delete_button.configure(text="Apagar ideia")
            self.right_tip_label.configure(text="Não precisa decorar atalhos: use os botões no alto da tela.")
        self._update_action_buttons()
        if hasattr(self, "edit_menu"):
            self._populate_edit_menu()
        if refresh:
            self.refresh_tree()
            self.canvas.redraw()

    def refresh_tree(self) -> None:
        self._syncing_tree = True
        try:
            self.tree.delete(*self.tree.get_children())

            def add(node: MindNode, parent: str = "") -> None:
                label = node.text.replace("\n", " ")
                if node.note.strip():
                    label += "  •"
                item = self.tree.insert(parent, "end", iid=node.id, text=label, open=not node.collapsed)
                for child in self.map.children(node.id):
                    add(child, item)

            if self.map.map_type == "conceptual":
                root = self.map.root
                root_label = root.text.replace("\n", " ")
                self.tree.insert("", "end", iid=root.id, text=root_label, open=True)
                for node in sorted(
                    (item for item in self.map.nodes.values() if item.id != root.id),
                    key=lambda item: item.text.casefold(),
                ):
                    label = node.text.replace("\n", " ")
                    if node.note.strip():
                        label += "  •"
                    self.tree.insert(root.id, "end", iid=node.id, text=label)
            else:
                add(self.map.root)
            if self.selected_id in self.map.nodes:
                self.tree.selection_set(self.selected_id)
                self.tree.see(self.selected_id)
        finally:
            self._syncing_tree = False

    def _on_tree_select(self, _event: tk.Event) -> None:
        if self._syncing_tree:
            return
        selection = self.tree.selection()
        # selection_set() em refresh_tree também emite <<TreeviewSelect>>.
        # Sem esta guarda, a própria atualização da interface chamaria
        # select_node() repetidamente e poderia travar a janela na abertura.
        if selection and selection[0] in self.map.nodes and selection[0] != self.selected_id:
            self.select_node(selection[0], focus_canvas=True)

    def select_node(self, node_id: str, focus_canvas: bool = False) -> None:
        if node_id not in self.map.nodes:
            return
        self.selected_id = node_id
        node = self.map.nodes[node_id]
        self.node_name_var.set(node.text)
        self.note_text.delete("1.0", "end")
        self.note_text.insert("1.0", node.note)
        if self.map.map_type == "conceptual":
            relationships = self.map.relationships_for(node.id)
            if relationships:
                lines: list[str] = []
                for relationship in relationships:
                    if relationship.source_id == node.id:
                        other = self.map.nodes[relationship.target_id]
                        lines.append(f"{relationship.label} → {other.text}")
                    else:
                        other = self.map.nodes[relationship.source_id]
                        lines.append(f"{other.text} → {relationship.label}")
                self.parent_var.set("\n".join(lines))
            else:
                self.parent_var.set("Sem relações ainda. Use Conectar para criar uma.")
            self.selected_badge.configure(text="Conceito central" if node.id == self.map.root_id else "Conceito selecionado")
        elif node.parent_id:
            self.parent_var.set(self.map.nodes[node.parent_id].text)
            self.selected_badge.configure(text="Ideia selecionada")
        else:
            self.parent_var.set("Esta é a ideia principal do mapa")
            self.selected_badge.configure(text="Ideia principal")
        self.refresh_tree()
        self.canvas.redraw()
        self._refresh_swatches()
        self.status_var.set(f"Selecionada: {node.text}")
        self._update_action_buttons()
        if focus_canvas:
            self.canvas.focus_set()

    def focus_node_name(self) -> None:
        self.node_name_entry.focus_set()
        self.node_name_entry.selection_range(0, "end")
        self.node_name_entry.icursor("end")

    def apply_map_title(self) -> bool:
        title = self.title_var.get().strip() or "Novo mapa"
        if title != self.map.title:
            self._remember_undo()
            self.map.title = title
            self.title_var.set(title)
            self.mark_changed("Nome do mapa atualizado — alterações não salvas")
        return True

    def apply_inspector(self, quiet: bool = False) -> bool:
        if self.selected_id not in self.map.nodes:
            return False
        node = self.map.nodes[self.selected_id]
        new_text = self.node_name_var.get().strip() or "Ideia sem nome"
        new_note = self.note_text.get("1.0", "end-1c").strip()
        changed = node.text != new_text or node.note != new_note
        if changed:
            self._remember_undo()
        node.text = new_text
        node.note = new_note
        if changed:
            self.mark_changed("Detalhes atualizados — alterações não salvas")
        self.refresh_tree()
        self.canvas.redraw()
        if not quiet:
            self.status_var.set("Alterações aplicadas à ideia selecionada.")
        return True

    def set_selected_color(self, color: str) -> None:
        if self.selected_id not in self.map.nodes:
            return
        node = self.map.nodes[self.selected_id]
        if node.color != color:
            self._remember_undo()
            node.color = color
            self.mark_changed("Cor da ideia atualizada — alterações não salvas")
            self.canvas.redraw()
            self._refresh_swatches()

    def add_child(self) -> None:
        if self.map.map_type == "conceptual":
            self.add_concept()
            return
        self.apply_inspector(quiet=True)
        parent_id = self.selected_id if self.selected_id in self.map.nodes else self.map.root_id
        self._remember_undo()
        child = self.map.add_child(parent_id)
        self.select_node(child.id)
        self.mark_changed("Nova subideia criada — escreva o nome dela")
        self.root.after_idle(self.focus_node_name)

    def add_sibling(self) -> None:
        if self.map.map_type == "conceptual":
            self.add_concept()
            return
        self.apply_inspector(quiet=True)
        if self.selected_id == self.map.root_id:
            self.add_child()
            return
        self._remember_undo()
        sibling = self.map.add_sibling(self.selected_id)
        self.select_node(sibling.id)
        self.mark_changed("Nova ideia criada — escreva o nome dela")
        self.root.after_idle(self.focus_node_name)

    def add_concept(self) -> None:
        if self.map.map_type != "conceptual":
            self.add_child()
            return
        self.apply_inspector(quiet=True)
        self._remember_undo()
        concept = self.map.add_concept()
        self.select_node(concept.id)
        self.mark_changed("Novo conceito criado — escreva o nome dele")
        self.root.after_idle(self.focus_node_name)

    def open_relationship_dialog(self) -> None:
        if self.map.map_type != "conceptual":
            self.status_var.set("As relações nomeadas ficam disponíveis em mapas conceituais.")
            return
        existing = self._relationship_dialog
        if existing is not None:
            try:
                if existing.winfo_exists():
                    # Mesmo que um atalho seja pressionado de novo, há sempre
                    # apenas uma janela de ligação aberta.
                    existing.deiconify()
                    existing.lift()
                    existing.focus_force()
                    return
            except tk.TclError:
                pass
            self._relationship_dialog = None
        concepts = list(self.map.nodes.values())
        if len(concepts) < 2:
            messagebox.showinfo(APP_NAME, "Crie pelo menos mais um conceito antes de adicionar uma relação.", parent=self.root)
            return

        choices = self.map.concept_choice_labels()
        labels = list(choices)
        source_default = next((label for label, node_id in choices.items() if node_id == self.selected_id), labels[0])
        target_default = next(label for label in labels if choices[label] != choices[source_default])

        dialog = tk.Toplevel(self.root)
        dialog.title("Conectar conceitos")
        dialog.geometry("540x455")
        dialog.resizable(False, False)
        dialog.configure(bg=self.palette["bg"])
        dialog.transient(self.root)
        self._grab_when_visible(dialog)
        self._relationship_dialog = dialog

        def close_dialog() -> None:
            if self._relationship_dialog is dialog:
                self._relationship_dialog = None
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
            try:
                if dialog.winfo_exists():
                    dialog.destroy()
            except tk.TclError:
                pass

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)

        container = ttk.Frame(dialog, style="Panel.TFrame", padding=22)
        container.pack(fill="both", expand=True, padx=14, pady=14)
        ttk.Label(container, text="Conectar conceitos", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(container, text="Escolha as duas caixas pelo texto que está dentro delas, escreva a frase da seta e confirme.", style="Hint.TLabel", wraplength=470).pack(anchor="w", pady=(5, 5))
        ttk.Label(container, text="A leitura será: origem → relação → destino.", style="Hint.TLabel", wraplength=470).pack(anchor="w", pady=(0, 19))

        source_var = tk.StringVar(value=source_default)
        target_var = tk.StringVar(value=target_default)
        relation_var = tk.StringVar(value="relaciona-se com")
        ttk.Label(container, text="De", style="Field.TLabel").pack(anchor="w")
        source_box = ttk.Combobox(container, textvariable=source_var, values=labels, state="readonly")
        source_box.pack(fill="x", pady=(4, 11))
        ttk.Label(container, text="Relação", style="Field.TLabel").pack(anchor="w")
        relation_entry = ttk.Entry(container, textvariable=relation_var, style="Inspector.TEntry")
        relation_entry.pack(fill="x", pady=(4, 11))
        ttk.Label(container, text="Para", style="Field.TLabel").pack(anchor="w")
        target_box = ttk.Combobox(container, textvariable=target_var, values=labels, state="readonly")
        target_box.pack(fill="x", pady=(4, 17))

        actions = ttk.Frame(container, style="Panel.TFrame")
        actions.pack(fill="x")

        def save_relationship() -> None:
            source_id = choices.get(source_var.get())
            target_id = choices.get(target_var.get())
            if source_id is None or target_id is None:
                messagebox.showerror(APP_NAME, "Escolha os dois conceitos antes de criar a ligação.", parent=dialog)
                return
            snapshot = self._history_snapshot()
            try:
                relationship = self.map.add_relationship(source_id, target_id, relation_var.get())
            except MapValidationError as error:
                messagebox.showerror(APP_NAME, str(error), parent=dialog)
                return
            self._remember_undo(snapshot)
            close_dialog()
            self.mark_changed("Relação conceitual criada — alterações não salvas")
            self.select_node(relationship.source_id)
            self.canvas.redraw()

        def submit_relationship(_event: tk.Event | None = None) -> str:
            save_relationship()
            return "break"

        ttk.Button(actions, text="Cancelar", command=close_dialog, style="Quiet.TButton").pack(side="right")
        ttk.Button(actions, text="Criar ligação", command=save_relationship, style="Primary.TButton").pack(side="right", padx=(0, 7))
        # Enter confirma este formulário inteiro. O retorno "break" impede
        # que o mesmo Enter chegue ao atalho do mapa que ficou atrás do diálogo.
        relation_entry.bind("<Return>", submit_relationship)
        relation_entry.bind("<KP_Enter>", submit_relationship)
        dialog.bind("<Return>", submit_relationship)
        dialog.bind("<KP_Enter>", submit_relationship)
        dialog.bind("<Escape>", lambda _event: (close_dialog(), "break")[1])
        relation_entry.focus_set()

    def toggle_collapsed(self) -> None:
        if self.map.map_type == "conceptual":
            self.status_var.set("Mapas conceituais mostram todos os conceitos e relações ao mesmo tempo.")
            return
        if self.selected_id not in self.map.nodes:
            return
        node = self.map.nodes[self.selected_id]
        if not self.map.children(node.id):
            self.status_var.set("Esta ideia ainda não possui subideias para recolher.")
            return
        self._remember_undo()
        node.collapsed = not node.collapsed
        self.mark_changed("Estrutura do mapa atualizada — alterações não salvas")
        self.refresh_tree()
        self.canvas.redraw()

    def organize_map(self) -> None:
        self.apply_inspector(quiet=True)
        self._remember_undo()
        self.map.arrange()
        self.mark_changed("Mapa organizado automaticamente — alterações não salvas")
        self.canvas.fit_map()

    def delete_selected(self) -> None:
        if self.selected_id == self.map.root_id:
            principal = "conceito central" if self.map.map_type == "conceptual" else "ideia principal"
            messagebox.showinfo(APP_NAME, f"O {principal} não pode ser apagado. Você pode renomeá-lo ou criar um novo mapa.", parent=self.root)
            return
        node = self.map.nodes[self.selected_id]
        count = len(self.map.descendants(node.id))
        if self.map.map_type == "conceptual":
            extra = " e todas as relações ligadas a ele"
            item_name = "conceito"
        else:
            extra = f" e suas {count} subideia(s)" if count else ""
            item_name = "ideia"
        if not messagebox.askyesno(
            APP_NAME,
            f"Apagar {item_name} “{node.text}”{extra}?\n\nVocê poderá desfazer pelo botão Desfazer ou com Ctrl+Z.",
            icon="warning",
            parent=self.root,
        ):
            return
        parent_id = node.parent_id or self.map.root_id
        self._remember_undo()
        self.map.delete_branch(node.id)
        self.selected_id = parent_id
        self.mark_changed(f"{item_name.capitalize()} apagado — alterações não salvas")
        self.select_node(parent_id)

    def mark_changed(self, message: str = "Alterações não salvas") -> None:
        self.dirty = True
        self.status_var.set(message)
        self._update_window_title()
        if self._autosave_after:
            try:
                self.root.after_cancel(self._autosave_after)
            except tk.TclError:
                pass
        self._autosave_after = self.root.after(2500, self._autosave)

    def _history_snapshot(self) -> dict:
        """Guarda o estado do documento antes de uma ação que o modifica."""
        return {
            "document": self.map.to_dict(self.theme_name),
            "selected_id": self.selected_id,
        }

    def _remember_undo(self, snapshot: dict | None = None) -> None:
        state = snapshot or self._history_snapshot()
        # Alguns fluxos aplicam o painel antes de criar uma ideia. Não repita
        # a mesma posição na pilha só porque a ação seguinte começou logo após.
        if self._undo_stack and self._undo_stack[-1] == state:
            return
        self._undo_stack.append(state)
        if len(self._undo_stack) > 60:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self._update_history_controls()

    def _reset_history(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._update_history_controls()

    def _update_history_controls(self) -> None:
        if hasattr(self, "undo_button"):
            self.undo_button.configure(state="normal" if self._undo_stack else "disabled")
        if hasattr(self, "edit_menu"):
            self._populate_edit_menu()

    def _restore_history(self, snapshot: dict, message: str) -> None:
        restored, _saved_theme = MindMap.from_dict(snapshot["document"])
        self.map = restored
        previous_selection = str(snapshot.get("selected_id", self.map.root_id))
        self.selected_id = previous_selection if previous_selection in self.map.nodes else self.map.root_id
        self.title_var.set(self.map.title)
        self._update_map_type_ui(refresh=False)
        self.select_node(self.selected_id)
        self.mark_changed(message)

    def undo(self) -> None:
        if not self._undo_stack:
            self.status_var.set("Nada para desfazer ainda.")
            return
        self._redo_stack.append(self._history_snapshot())
        previous = self._undo_stack.pop()
        self._restore_history(previous, "Última alteração desfeita — alterações não salvas")
        self._update_history_controls()

    def _update_window_title(self) -> None:
        marker = " •" if self.dirty else ""
        self.root.title(f"{self.map.title}{marker} — {APP_NAME} {APP_VERSION}")

    def _autosave(self) -> None:
        self._autosave_after = None
        if not self.dirty:
            return
        try:
            self.map.save(self.autosave_path, self.theme_name)
        except OSError:
            return

    def _remove_recovery(self) -> None:
        try:
            self.autosave_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _offer_recovery(self) -> None:
        if not self.autosave_path.exists():
            return
        try:
            recovered_map, recovered_theme = MindMap.load(self.autosave_path)
        except (OSError, MapValidationError):
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Encontramos um rascunho recuperável do {APP_NAME}. Deseja abri-lo?",
            parent=self.root,
        ):
            self._remove_recovery()
            return
        self.map = recovered_map
        self._recovery_loaded = True
        self.file_path = None
        self.selected_id = self.map.root_id
        self.title_var.set(self.map.title)
        self._reset_history()
        if recovered_theme in THEMES:
            self.apply_theme(recovered_theme)
        self._update_map_type_ui(refresh=False)
        self.dirty = True
        self._update_window_title()
        self.select_node(self.selected_id)
        self.canvas.fit_map()
        self.status_var.set("Rascunho recuperado. Salve-o para escolher onde guardar.")

    def _confirm_leave_unsaved(self, discard_recovery: bool = False) -> bool:
        if not self.dirty:
            return True
        choice = messagebox.askyesnocancel(
            APP_NAME,
            "Há alterações não salvas. Deseja salvar antes de continuar?",
            parent=self.root,
        )
        if choice is None:
            return False
        if choice:
            return self.save()
        if discard_recovery:
            self._remove_recovery()
        return True

    def _offer_map_type_choice(self) -> None:
        if self._recovery_loaded or self.file_path is not None or self.dirty or len(self.map.nodes) != 1:
            return
        self.show_map_type_dialog()

    def show_map_type_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Escolha o tipo de mapa")
        dialog.geometry("700x360")
        dialog.minsize(640, 330)
        dialog.configure(bg=self.palette["bg"])
        dialog.transient(self.root)
        self._grab_when_visible(dialog)

        container = ttk.Frame(dialog, style="App.TFrame", padding=(14, 12))
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="Que tipo de mapa você quer criar?", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(container, text="Cada formato deixa os botões e a organização mais adequados ao seu objetivo.", style="Hint.TLabel").pack(anchor="w", pady=(4, 10))
        choices = ttk.Frame(container, style="App.TFrame")
        choices.pack(fill="both", expand=True)

        def card(parent: tk.Misc, title: str, summary: str, points: str, action: str, map_type: str) -> None:
            frame = self._group(parent, title)
            frame.pack(side="left", fill="both", expand=True, padx=4)
            ttk.Label(frame, text=summary, style="Value.TLabel", wraplength=280).pack(anchor="w", pady=(0, 8))
            ttk.Label(frame, text=points, style="Hint.TLabel", wraplength=280, justify="left").pack(anchor="w", fill="x", expand=True)
            ttk.Button(frame, text=action, command=lambda: choose(map_type), style="Primary.TButton").pack(anchor="w", pady=(10, 0))

        def choose(map_type: str) -> None:
            dialog.destroy()
            self._create_new_map(map_type)

        card(
            choices,
            "Mapa mental",
            "Parte de uma ideia principal e abre ramos para organizar pensamentos, estudos ou planos.",
            "• Ideia central\n• Subideias em ramos\n• Organização para os lados",
            "Criar mapa mental",
            "mind",
        )
        card(
            choices,
            "Mapa conceitual",
            "Conecta conceitos por frases, mostrando a relação lógica entre eles sem obrigar uma hierarquia.",
            "• Conceitos avulsos\n• Setas com relação nomeada\n• Organização de cima para baixo",
            "Criar mapa conceitual",
            "conceptual",
        )

    def _create_new_map(self, map_type: str) -> None:
        self.map = MindMap.new(map_type=map_type)
        self.file_path = None
        self.selected_id = self.map.root_id
        self.title_var.set(self.map.title)
        self.dirty = False
        self._reset_history()
        self._remove_recovery()
        self._update_map_type_ui(refresh=False)
        self.select_node(self.selected_id)
        self.root.after_idle(self.canvas.center_on_root)
        description = "mapa conceitual" if map_type == "conceptual" else "mapa mental"
        self.status_var.set(f"Novo {description} pronto. Comece pelo conceito central." if map_type == "conceptual" else "Novo mapa mental pronto. Comece pela ideia principal.")
        self._update_window_title()

    def new_map(self) -> bool:
        if not self._confirm_leave_unsaved():
            return False
        self.show_map_type_dialog()
        return True

    def open_map(self) -> bool:
        if not self._confirm_leave_unsaved():
            return False
        filename = filedialog.askopenfilename(
            parent=self.root,
            title=f"Abrir mapa {APP_NAME}",
            filetypes=[
                (f"Mapas {APP_NAME}", f"*{MAP_FILE_EXTENSION}"),
                ("Mapas NexMind (compatibilidade)", " ".join(f"*{extension}" for extension in LEGACY_MAP_EXTENSIONS)),
                ("Arquivos JSON", "*.json"),
                ("Todos os arquivos", "*.*"),
            ],
        )
        if not filename:
            return False
        try:
            mind_map, theme = MindMap.load(filename)
        except (OSError, MapValidationError) as error:
            messagebox.showerror(APP_NAME, f"Não foi possível abrir este mapa.\n\n{error}", parent=self.root)
            return False
        self.map = mind_map
        self.file_path = Path(filename)
        self.selected_id = self.map.root_id
        self.title_var.set(self.map.title)
        self.dirty = False
        self._reset_history()
        self._remove_recovery()
        self._update_map_type_ui(refresh=False)
        if theme in THEMES:
            self.apply_theme(theme)
        self.select_node(self.selected_id)
        self.root.after_idle(self.canvas.fit_map)
        self.status_var.set(f"Mapa aberto: {self.file_path.name}")
        self._update_window_title()
        return True

    def save(self) -> bool:
        self.apply_map_title()
        self.apply_inspector(quiet=True)
        if self.file_path is None:
            return self.save_as()
        try:
            self.map.save(self.file_path, self.theme_name)
        except OSError as error:
            messagebox.showerror(APP_NAME, f"Não foi possível salvar o mapa.\n\n{error}", parent=self.root)
            return False
        self.dirty = False
        self._remove_recovery()
        self._update_window_title()
        self.status_var.set(f"Salvo em {self.file_path.name}")
        return True

    def save_as(self) -> bool:
        self.apply_map_title()
        self.apply_inspector(quiet=True)
        suggested = f"{self.map.title.strip() or 'Novo mapa'}{MAP_FILE_EXTENSION}"
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            title=f"Salvar mapa {APP_NAME}",
            initialfile=suggested,
            defaultextension=MAP_FILE_EXTENSION,
            filetypes=[(f"Mapas {APP_NAME}", f"*{MAP_FILE_EXTENSION}")],
        )
        if not filename:
            return False
        self.file_path = Path(filename)
        return self.save()

    def export_svg(self) -> bool:
        self.apply_inspector(quiet=True)
        suggested = f"{self.map.title.strip() or 'mapa'}.svg"
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            title="Exportar mapa como SVG",
            initialfile=suggested,
            defaultextension=".svg",
            filetypes=[("Imagem vetorial SVG", "*.svg")],
        )
        if not filename:
            return False
        try:
            Path(filename).write_text(self._build_svg(), encoding="utf-8")
        except OSError as error:
            messagebox.showerror(APP_NAME, f"Não foi possível exportar a imagem.\n\n{error}", parent=self.root)
            return False
        self.status_var.set(f"SVG exportado: {Path(filename).name}")
        return True

    def export_pdf(self) -> bool:
        """Exporta uma cópia pronta para compartilhar ou imprimir."""
        self.apply_inspector(quiet=True)
        suggested = f"{self.map.title.strip() or 'mapa'}.pdf"
        filename = filedialog.asksaveasfilename(
            parent=self.root,
            title="Exportar mapa como PDF",
            initialfile=suggested,
            defaultextension=".pdf",
            filetypes=[("Documento PDF", "*.pdf")],
        )
        if not filename:
            return False
        target = Path(filename)
        try:
            if ReportLabCanvas is not None and HexColor is not None:
                self._write_pdf_with_reportlab(target)
            else:
                # O PDF básico garante a exportação até para quem executa o
                # código-fonte sem instalar bibliotecas extras.
                target.write_bytes(self._build_pdf())
        except (OSError, ValueError) as error:
            messagebox.showerror(APP_NAME, f"Não foi possível exportar o PDF.\n\n{error}", parent=self.root)
            return False
        self.status_var.set(f"PDF exportado: {target.name}")
        return True

    def _pdf_map_layout(self):
        """Calcula uma transformação do mapa para uma página A4 horizontal."""
        nodes = list(self.map.visible_nodes())
        if not nodes:
            return [], set(), 1.0, lambda x, y: (x, y)
        boxes = [self.canvas.world_box(node) for node in nodes]
        min_x = min(box[0] for box in boxes) - 52
        min_y = min(box[1] for box in boxes) - 52
        max_x = max(box[2] for box in boxes) + 52
        max_y = max(box[3] for box in boxes) + 52
        world_width = max(1.0, max_x - min_x)
        world_height = max(1.0, max_y - min_y)
        margin = 34.0
        title_space = 52.0
        available_width = PDF_PAGE_WIDTH - margin * 2
        available_height = PDF_PAGE_HEIGHT - margin - title_space
        scale = max(0.04, min(1.5, available_width / world_width, available_height / world_height))
        offset_x = margin + (available_width - world_width * scale) / 2 - min_x * scale
        offset_y = margin + (available_height - world_height * scale) / 2 + max_y * scale

        def to_pdf(x: float, y: float) -> tuple[float, float]:
            # O mapa cresce para baixo; a página PDF cresce para cima.
            return offset_x + x * scale, offset_y - y * scale

        return nodes, {node.id for node in nodes}, scale, to_pdf

    def _write_pdf_with_reportlab(self, target: Path) -> None:
        """Cria um PDF visual de alta qualidade quando ReportLab estiver disponível."""
        if ReportLabCanvas is None or HexColor is None:
            target.write_bytes(self._build_pdf())
            return

        page = ReportLabCanvas(str(target), pagesize=(PDF_PAGE_WIDTH, PDF_PAGE_HEIGHT), pageCompression=1)
        page.setTitle(_pdf_display_text(self.map.title))
        page.setAuthor(APP_NAME)
        page.setFillColor(HexColor("#FFFFFF"))
        page.rect(0, 0, PDF_PAGE_WIDTH, PDF_PAGE_HEIGHT, fill=1, stroke=0)
        page.setFillColor(HexColor("#17233A"))
        page.setFont("Helvetica-Bold", 16)
        page.drawString(34, PDF_PAGE_HEIGHT - 30, _pdf_display_text(self.map.title)[:88])
        page.setFont("Helvetica", 8.5)
        page.setFillColor(HexColor("#62718A"))
        page.drawRightString(PDF_PAGE_WIDTH - 34, PDF_PAGE_HEIGHT - 28, f"Feito com {APP_NAME}")

        nodes, visible_ids, scale, to_pdf = self._pdf_map_layout()
        connection_color = HexColor("#65758B")
        page.setStrokeColor(connection_color)
        page.setFillColor(connection_color)
        page.setLineWidth(max(0.7, 1.65 * scale))

        if self.map.map_type == "mind":
            for node in nodes:
                if not node.parent_id or node.parent_id not in visible_ids:
                    continue
                parent = self.map.nodes[node.parent_id]
                sx, sy, ex, ey = self._svg_connection_endpoints(parent, node)
                bend = (sx + ex) / 2
                start = to_pdf(sx, sy)
                control_one = to_pdf(bend, sy)
                control_two = to_pdf(bend, ey)
                end = to_pdf(ex, ey)
                path = page.beginPath()
                path.moveTo(*start)
                path.curveTo(*control_one, *control_two, *end)
                page.drawPath(path, stroke=1, fill=0)
        else:
            for relationship in self.map.relationships.values():
                if relationship.source_id not in visible_ids or relationship.target_id not in visible_ids:
                    continue
                source = self.map.nodes[relationship.source_id]
                target_node = self.map.nodes[relationship.target_id]
                sx, sy, ex, ey = self._svg_connection_endpoints(source, target_node)
                start = to_pdf(sx, sy)
                end = to_pdf(ex, ey)
                page.line(*start, *end)
                angle = math.atan2(end[1] - start[1], end[0] - start[0])
                arrow_size = max(4.0, 7.0 * scale)
                left = (
                    end[0] - arrow_size * math.cos(angle - math.pi / 6),
                    end[1] - arrow_size * math.sin(angle - math.pi / 6),
                )
                right = (
                    end[0] - arrow_size * math.cos(angle + math.pi / 6),
                    end[1] - arrow_size * math.sin(angle + math.pi / 6),
                )
                arrow = page.beginPath()
                arrow.moveTo(*end)
                arrow.lineTo(*left)
                arrow.lineTo(*right)
                arrow.close()
                page.drawPath(arrow, stroke=0, fill=1)
                midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2 + max(7, 11 * scale))
                page.setFont("Helvetica-Bold", max(6.5, min(10.5, 8.5 * scale)))
                page.drawCentredString(midpoint[0], midpoint[1], _pdf_display_text(relationship.label)[:56])

        for node in nodes:
            x1, y1, x2, y2 = self.canvas.world_box(node)
            left, top = to_pdf(x1, y1)
            right, bottom = to_pdf(x2, y2)
            width, height = right - left, top - bottom
            text_color = HexColor(readable_text_color(node.color))
            page.setFillColor(HexColor(_safe_hex_color(node.color)))
            page.setStrokeColor(HexColor("#404040"))
            page.roundRect(left, bottom, width, height, 2, fill=1, stroke=1)
            page.setFillColor(text_color)
            lines = wrap_label(node.text)
            font_size = max(6.5, min(11.5, 10.5 * scale))
            line_gap = font_size * 1.25
            first_y = (top + bottom) / 2 + (len(lines) - 1) * line_gap / 2 - font_size * 0.34
            page.setFont("Helvetica-Bold", font_size)
            for index, line in enumerate(lines):
                page.drawCentredString((left + right) / 2, first_y - index * line_gap, _pdf_display_text(line))
            if node.note.strip():
                page.setFillColor(text_color)
                page.circle(right - max(4, 8 * scale), top - max(4, 8 * scale), max(1.5, 2.4 * scale), fill=1, stroke=0)

        page.save()

    def _build_pdf(self) -> bytes:
        """Gera um PDF vetorial simples sem depender de bibliotecas externas."""
        nodes, visible_ids, scale, to_pdf = self._pdf_map_layout()
        lines = [
            "1 1 1 rg",
            f"0 0 {PDF_PAGE_WIDTH:.2f} {PDF_PAGE_HEIGHT:.2f} re f",
            "0.09 0.14 0.23 rg",
            "BT /F2 16 Tf 34 565 Td (" + _pdf_literal(self.map.title)[:88] + ") Tj ET",
            "0.38 0.44 0.54 rg",
            f"BT /F1 8.5 Tf {PDF_PAGE_WIDTH - 118:.2f} 567 Td (Feito com {APP_NAME}) Tj ET",
        ]
        connection = _pdf_color("#65758B")
        line_width = max(0.7, 1.65 * scale)
        lines.append(f"{connection[0]:.3f} {connection[1]:.3f} {connection[2]:.3f} RG")
        lines.append(f"{line_width:.2f} w")

        if self.map.map_type == "mind":
            for node in nodes:
                if not node.parent_id or node.parent_id not in visible_ids:
                    continue
                parent = self.map.nodes[node.parent_id]
                sx, sy, ex, ey = self._svg_connection_endpoints(parent, node)
                bend = (sx + ex) / 2
                start = to_pdf(sx, sy)
                control_one = to_pdf(bend, sy)
                control_two = to_pdf(bend, ey)
                end = to_pdf(ex, ey)
                lines.append(
                    f"{start[0]:.2f} {start[1]:.2f} m {control_one[0]:.2f} {control_one[1]:.2f} "
                    f"{control_two[0]:.2f} {control_two[1]:.2f} {end[0]:.2f} {end[1]:.2f} c S"
                )
        else:
            for relationship in self.map.relationships.values():
                if relationship.source_id not in visible_ids or relationship.target_id not in visible_ids:
                    continue
                source = self.map.nodes[relationship.source_id]
                target_node = self.map.nodes[relationship.target_id]
                sx, sy, ex, ey = self._svg_connection_endpoints(source, target_node)
                start = to_pdf(sx, sy)
                end = to_pdf(ex, ey)
                lines.append(f"{start[0]:.2f} {start[1]:.2f} m {end[0]:.2f} {end[1]:.2f} l S")
                angle = math.atan2(end[1] - start[1], end[0] - start[0])
                arrow_size = max(4.0, 7.0 * scale)
                left = (
                    end[0] - arrow_size * math.cos(angle - math.pi / 6),
                    end[1] - arrow_size * math.sin(angle - math.pi / 6),
                )
                right = (
                    end[0] - arrow_size * math.cos(angle + math.pi / 6),
                    end[1] - arrow_size * math.sin(angle + math.pi / 6),
                )
                lines.append(f"{connection[0]:.3f} {connection[1]:.3f} {connection[2]:.3f} rg")
                lines.append(
                    f"{end[0]:.2f} {end[1]:.2f} m {left[0]:.2f} {left[1]:.2f} l {right[0]:.2f} {right[1]:.2f} l h f"
                )
                label_size = max(6.5, min(10.5, 8.5 * scale))
                label = _pdf_literal(relationship.label)[:56]
                label_x = (start[0] + end[0]) / 2 - _pdf_text_width(label, label_size) / 2
                label_y = (start[1] + end[1]) / 2 + max(7, 11 * scale)
                lines.append(f"BT /F2 {label_size:.2f} Tf {label_x:.2f} {label_y:.2f} Td ({label}) Tj ET")

        for node in nodes:
            x1, y1, x2, y2 = self.canvas.world_box(node)
            left, top = to_pdf(x1, y1)
            right, bottom = to_pdf(x2, y2)
            width, height = right - left, top - bottom
            color = _pdf_color(node.color)
            lines.append(f"{color[0]:.3f} {color[1]:.3f} {color[2]:.3f} rg")
            lines.append(f"{left:.2f} {bottom:.2f} {width:.2f} {height:.2f} re f")
            font_size = max(6.5, min(11.5, 10.5 * scale))
            line_gap = font_size * 1.25
            text_lines = wrap_label(node.text)
            first_y = (top + bottom) / 2 + (len(text_lines) - 1) * line_gap / 2 - font_size * 0.34
            text_rgb = _pdf_color(readable_text_color(node.color))
            lines.append(f"{text_rgb[0]:.3f} {text_rgb[1]:.3f} {text_rgb[2]:.3f} rg")
            for index, text_line in enumerate(text_lines):
                literal = _pdf_literal(text_line)
                text_x = (left + right) / 2 - _pdf_text_width(literal, font_size) / 2
                text_y = first_y - index * line_gap
                lines.append(f"BT /F2 {font_size:.2f} Tf {text_x:.2f} {text_y:.2f} Td ({literal}) Tj ET")

        stream = "\n".join(lines).encode("cp1252", "replace")
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PDF_PAGE_WIDTH:.2f} {PDF_PAGE_HEIGHT:.2f}] "
                "/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>"
            ).encode("ascii"),
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        ]
        document = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for index, obj in enumerate(objects, start=1):
            offsets.append(len(document))
            document.extend(f"{index} 0 obj\n".encode("ascii"))
            document.extend(obj)
            document.extend(b"\nendobj\n")
        xref_offset = len(document)
        document.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        document.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        document.extend(
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
        )
        return bytes(document)

    def _build_svg(self) -> str:
        nodes = list(self.map.visible_nodes())
        boxes = [self.canvas.world_box(node) for node in nodes]
        min_x = min(box[0] for box in boxes) - 76
        min_y = min(box[1] for box in boxes) - 76
        max_x = max(box[2] for box in boxes) + 76
        max_y = max(box[3] for box in boxes) + 76
        width = max_x - min_x
        height = max_y - min_y
        p = self.palette
        tx, ty = -min_x, -min_y
        visible_ids = {node.id for node in nodes}
        pieces = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">',
            f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="{p["connection"]}"/></marker></defs>',
            f'<rect width="100%" height="100%" fill="{p["canvas"]}"/>',
            '<g font-family="Tahoma, Segoe UI, Arial, sans-serif">',
        ]
        if self.map.map_type == "mind":
            for node in nodes:
                if not node.parent_id or node.parent_id not in visible_ids:
                    continue
                parent = self.map.nodes[node.parent_id]
                sx, sy, ex, ey = self._svg_connection_endpoints(parent, node)
                bend = (sx + ex) / 2
                pieces.append(
                    f'<path d="M {sx + tx:.1f} {sy + ty:.1f} C {bend + tx:.1f} {sy + ty:.1f}, {bend + tx:.1f} {ey + ty:.1f}, {ex + tx:.1f} {ey + ty:.1f}" fill="none" stroke="{p["connection"]}" stroke-width="2.5"/>'
                )
        else:
            for relationship in self.map.relationships.values():
                if relationship.source_id not in visible_ids or relationship.target_id not in visible_ids:
                    continue
                source = self.map.nodes[relationship.source_id]
                target = self.map.nodes[relationship.target_id]
                sx, sy, ex, ey = self._svg_connection_endpoints(source, target)
                pieces.append(
                    f'<line x1="{sx + tx:.1f}" y1="{sy + ty:.1f}" x2="{ex + tx:.1f}" y2="{ey + ty:.1f}" stroke="{p["connection"]}" stroke-width="2.5" marker-end="url(#arrow)"/>'
                )
                pieces.append(
                    f'<text x="{(sx + ex) / 2 + tx:.1f}" y="{(sy + ey) / 2 + ty - 10:.1f}" text-anchor="middle" fill="{p["text"]}" font-size="12" font-weight="700">{html.escape(relationship.label)}</text>'
                )
        for node in nodes:
            x1, y1, x2, y2 = self.canvas.world_box(node)
            w, h = x2 - x1, y2 - y1
            text_color = readable_text_color(node.color)
            pieces.append(f'<rect x="{x1 + tx:.1f}" y="{y1 + ty:.1f}" width="{w:.1f}" height="{h:.1f}" rx="2" fill="{node.color}" stroke="#404040" stroke-width="1"/>')
            lines = wrap_label(node.text)
            start_y = node.y + ty - ((len(lines) - 1) * 9)
            pieces.append(f'<text x="{node.x + tx:.1f}" y="{start_y:.1f}" text-anchor="middle" fill="{text_color}" font-size="14" font-weight="700">')
            for index, line in enumerate(lines):
                dy = "0" if index == 0 else "18"
                pieces.append(f'<tspan x="{node.x + tx:.1f}" dy="{dy}">{html.escape(line)}</tspan>')
            pieces.append("</text>")
            if node.note.strip():
                pieces.append(f'<circle cx="{x2 + tx - 16:.1f}" cy="{y1 + ty + 16:.1f}" r="4" fill="{text_color}" opacity="0.95"/>')
        pieces.extend(["</g>", "</svg>"])
        return "\n".join(pieces)

    def _svg_connection_endpoints(self, source: MindNode, target: MindNode) -> tuple[float, float, float, float]:
        source_box = self.canvas.world_box(source)
        target_box = self.canvas.world_box(target)
        dx, dy = target.x - source.x, target.y - source.y
        if abs(dx) >= abs(dy):
            if dx >= 0:
                return source_box[2], source.y, target_box[0], target.y
            return source_box[0], source.y, target_box[2], target.y
        if dy >= 0:
            return source.x, source_box[3], target.x, target_box[1]
        return source.x, source_box[1], target.x, target_box[3]

    def show_help(self, initial_tab: str = "manual") -> None:
        window = tk.Toplevel(self.root)
        window.title(f"Ajuda — {APP_NAME}")
        window.geometry("720x600")
        window.minsize(560, 440)
        window.configure(bg=self.palette["bg"])
        self._help_windows.append(window)
        window.protocol("WM_DELETE_WINDOW", lambda: self._close_help_window(window))
        window.transient(self.root)

        header = ttk.Frame(window, style="Header.TFrame", padding=(22, 16))
        header.pack(fill="x")
        ttk.Label(header, text="Central de ajuda", style="Brand.TLabel").pack(side="left")
        ttk.Label(header, text=f"{APP_NAME} v{APP_VERSION}", style="Version.TLabel").pack(side="right")
        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True, padx=16, pady=16)
        manual_tab = ttk.Frame(notebook, style="Panel.TFrame")
        notes_tab = ttk.Frame(notebook, style="Panel.TFrame")
        about_tab = ttk.Frame(notebook, style="Panel.TFrame")
        notebook.add(manual_tab, text="Manual")
        notebook.add(notes_tab, text="Notas da versão")
        notebook.add(about_tab, text="Sobre")
        manual = self._help_text(manual_tab, self._manual_text())
        notes = self._help_text(notes_tab, self._notes_text())
        about = self._help_text(about_tab, self._about_text())
        _ = manual, notes, about
        if initial_tab == "notes":
            notebook.select(notes_tab)
        elif initial_tab == "about":
            notebook.select(about_tab)
        else:
            notebook.select(manual_tab)

    def _close_help_window(self, window: tk.Toplevel) -> None:
        if window in self._help_windows:
            self._help_windows.remove(window)
        window.destroy()

    def _help_text(self, master: tk.Misc, contents: str) -> ScrolledText:
        text = self._make_text(master, wrap="word")
        text.configure(padx=14, pady=10)
        text.pack(fill="both", expand=True)
        text.insert("1.0", contents)
        text.configure(state="disabled")
        return text

    def _manual_text(self) -> str:
        return f"""Bem-vindo ao {APP_NAME}

O {APP_NAME} transforma uma ideia em um mapa visual sem exigir experiência com diagramas. Ele funciona offline e salva seus mapas em arquivos {MAP_FILE_EXTENSION}.

ESCOLHA O TIPO DE MAPA

Ao abrir o programa ou usar Arquivo > Novo mapa, escolha um formato:

• Mapa mental: começa em uma ideia principal e abre ramos. É ótimo para resumir uma aula, organizar tarefas ou planejar algo.
• Mapa conceitual: liga conceitos por frases e setas. É melhor para explicar causas, processos e relações.

COMEÇANDO UM MAPA MENTAL

Você não precisa decorar atalhos. Faça assim:

1. Clique na caixa “Ideia principal”.
2. Clique em “+ Subideia” no alto da janela.
3. Escreva o nome da caixa nova, à direita, e clique em “Aplicar alterações”.

Para criar outra ideia no mesmo nível, selecione uma ideia que não seja a principal e clique em “+ Outra ideia”. Quando quiser, arraste as caixas ou clique em “Organizar”.

COMEÇANDO UM MAPA CONCEITUAL

Clique em “+ Conceito” para criar as caixas. Depois clique em “Conectar”. A janela mostra o texto que está dentro de cada caixa — por exemplo, “1”, “2” e “3” — para você escolher sem precisar reconhecer códigos. A leitura da seta é sempre:

origem → relação → destino

Exemplo: “Plantas → servem de alimento para → Herbívoros”. Clique em “Criar ligação” para confirmar. Dentro dessa janela, Enter confirma a ligação; ele não abre outra janela.

EDITANDO UMA CAIXA

Selecione uma caixa. À direita você pode mudar o título, escrever uma nota e escolher uma cor. Clique em “Aplicar alterações” para confirmar. Um ponto discreto no título indica que a caixa possui uma nota.

NAVEGAÇÃO E ZOOM

Clique e arraste uma caixa para reposicioná-la. Arraste o fundo para navegar. Use os botões + e − no topo do mapa para aproximar ou afastar; “Ajustar” mostra o mapa inteiro. Ctrl + roda do mouse também amplia ou reduz onde o cursor está.

SALVANDO E EXPORTANDO

Salvar cria um arquivo {MAP_FILE_EXTENSION}, que pode ser aberto novamente no {APP_NAME}. Arquivo > Exportar como PDF cria uma cópia pronta para enviar ou imprimir. “Exportar como SVG” cria uma imagem vetorial nítida, boa para abrir em outros programas.

O {APP_NAME} também abre mapas antigos do NexMind com extensão .nexmind.

DESFAZER

Se algo saiu errado, clique em “Desfazer” no alto da janela. Também é possível usar Ctrl+Z com o mapa ou a área de estrutura selecionados. Enquanto você estiver escrevendo em um campo, Ctrl+Z continua reservado ao próprio texto.

ATALHOS OPCIONAIS

Os botões são o jeito mais simples de usar o programa. Se preferir o teclado, clique primeiro no mapa ou na estrutura: Tab cria uma subideia (ou um conceito), Enter cria uma ideia no mesmo nível no mapa mental e F2 edita. Delete apaga a caixa selecionada.

SEGURANÇA

Enquanto houver alterações não salvas, o {APP_NAME} cria um rascunho recuperável. Na próxima abertura, ele oferece restaurar esse trabalho.
"""

    def _notes_text(self) -> str:
        return f"""Notas da versão — {APP_VERSION}

Lançamento oficial do {APP_NAME}.

O que chegou nesta versão

• Nova identidade: nome ThinkTrellis, logo oficial e ícone do programa.
• Os mapas novos agora usam a extensão {MAP_FILE_EXTENSION}; arquivos NexMind antigos (.nexmind) continuam podendo ser abertos.
• A janela “Conectar conceitos” agora mostra o texto real de cada caixa. Se as caixas forem “1”, “2” e “3”, é isso que aparece na lista — sem códigos técnicos.
• Quando dois conceitos tiverem o mesmo nome, eles recebem apenas uma numeração simples, como “Novo conceito (1)” e “Novo conceito (2)”.
• A confirmação da ligação foi reforçada: há somente uma janela aberta, o botão Criar ligação fica visível e Enter confirma a ação em vez de abrir outra janela.
• O painel “Comece por aqui” foi reescrito em passos claros para quem está aprendendo a usar o computador.
• Mantidos tema claro/escuro, PDF, SVG, rascunho recuperável, mover caixas, organizar automaticamente, Ctrl+Z e os dois tipos de mapa.

O {APP_NAME} continua sendo um programa offline e não exige Node.js, Electron ou internet para criar e abrir mapas.
"""

    def _about_text(self) -> str:
        return f"""{APP_NAME} {APP_VERSION}

Um editor de mapas mentais e conceituais para computador, pensado para quem está começando e quer organizar ideias sem uma tela cheia de termos técnicos.

Tecnologia

Esta versão usa apenas Python e a biblioteca gráfica nativa Tkinter. Não depende de Node.js, Electron ou conexão com a internet para funcionar.

Formato de arquivo

Os mapas são guardados em {MAP_FILE_EXTENSION}, um JSON simples e legível. Isso ajuda a manter os dados leves, portáveis e recuperáveis. O programa também abre arquivos NexMind antigos (.nexmind).

Créditos

{COPYRIGHT_NOTICE}
{DEVELOPER_CREDIT}
"""

    def show_about(self) -> None:
        self.show_help("about")

    def on_close(self) -> None:
        if not self._confirm_leave_unsaved(discard_recovery=True):
            return
        self._write_preferences()
        self.root.destroy()

    def smoke_test(self) -> None:
        """Abre a janela, desenha os dois tipos de mapa, troca de visual e exporta.

        Serve para conferir, sem ninguém clicando, que o programa (inclusive o
        executável gerado) inicia e desenha direito. Erros que o Tk normalmente
        só imprime dentro de eventos também fazem o teste falhar.
        """
        errors: list[str] = []

        def collect(exc_type, exc_value, exc_traceback) -> None:
            errors.append("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))

        self.root.report_callback_exception = collect
        try:
            self.root.update()
            for map_type in ("mind", "conceptual"):
                self._create_new_map(map_type)
                if map_type == "mind":
                    first = self.map.add_child(self.map.root_id, "Primeira ideia")
                    self.map.add_child(first.id, "Detalhe")
                    self.map.add_sibling(first.id, "Segunda ideia")
                else:
                    source = self.map.add_concept("Luz")
                    target = self.map.add_concept("Prisma")
                    self.map.add_relationship(source.id, target.id, "atravessa")
                self.map.arrange()
                self.refresh_tree()
                self.select_node(self.map.root_id)
                self.canvas.fit_map()
                for theme_name in THEMES:
                    self.apply_theme(theme_name, persist=False)
                    self.root.update()
            with tempfile.TemporaryDirectory() as folder:
                target_folder = Path(folder)
                (target_folder / "mapa.svg").write_text(self._build_svg(), encoding="utf-8")
                (target_folder / "mapa.pdf").write_bytes(self._build_pdf())
                self._write_pdf_with_reportlab(target_folder / "mapa-reportlab.pdf")
            self.root.update()
        finally:
            self.root.destroy()
        if errors:
            raise RuntimeError("Erros dentro da interface:\n" + "\n".join(errors))

    def run(self) -> None:
        self.root.mainloop()
