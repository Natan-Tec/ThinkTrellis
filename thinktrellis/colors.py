"""Funções de cor que não dependem da interface gráfica."""

from __future__ import annotations


def safe_hex_color(color: str, default: str = "#17233A") -> str:
    """Devolve ``color`` se for um hexadecimal ``#RRGGBB`` válido; senão, ``default``."""
    raw = str(color).strip().lstrip("#")
    if len(raw) == 6:
        try:
            int(raw, 16)
            return f"#{raw}"
        except ValueError:
            pass
    return default


def _linear(channel: int) -> float:
    value = channel / 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(color: str) -> float:
    raw = safe_hex_color(color, "#000000").lstrip("#")
    red, green, blue = (int(raw[index:index + 2], 16) for index in (0, 2, 4))
    return 0.2126 * _linear(red) + 0.7152 * _linear(green) + 0.0722 * _linear(blue)


def contrast_ratio(first: str, second: str) -> float:
    """Razão de contraste (1 a 21) entre duas cores, como nas diretrizes WCAG."""
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def readable_text_color(background: str) -> str:
    """Escolhe branco ou preto para o texto sobre a cor de fundo informada.

    Branco continua sendo a preferência para cores médias e escuras (azul,
    violeta, rosa). Cores claras, como amarelo e verde, recebem texto preto,
    que fica bem mais legível.
    """
    return "#FFFFFF" if contrast_ratio("#FFFFFF", background) >= 3.5 else "#000000"
