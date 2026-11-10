"""Teste de ponta a ponta da interface: abre a janela de verdade.

Precisa de Tkinter e de uma tela. No Linux sem tela use `xvfb-run -a`.
Sem isso, o teste é pulado em vez de falhar.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

try:
    import tkinter as tk

    from thinktrellis.app import ThinkTrellisApp
except ImportError:  # pragma: no cover - depende da instalação do Python.
    tk = None  # type: ignore[assignment]
    ThinkTrellisApp = None  # type: ignore[assignment,misc]


@unittest.skipIf(tk is None, "Tkinter não está instalado")
class InterfaceSmokeTest(unittest.TestCase):
    def test_window_opens_draws_both_map_types_and_exports(self) -> None:
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"THINKTRELLIS_DATA_DIR": folder}):
            try:
                app = ThinkTrellisApp()
            except tk.TclError as error:
                # Só pula quando realmente não há tela. Qualquer outro erro do
                # Tk é um defeito da interface e precisa derrubar o teste.
                if "display" in str(error).lower():
                    self.skipTest(f"Sem tela para abrir a janela: {error}")
                raise
            app.smoke_test()


if __name__ == "__main__":
    unittest.main()
