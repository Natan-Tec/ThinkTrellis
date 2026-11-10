#!/usr/bin/env bash
# Gera dist/ThinkTrellis (executável único) no Linux.
# Precisa de Python 3.10+ com Tkinter (pacote "python3-tk" no Debian/Ubuntu).
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install -r requirements-build.txt
python3 -m PyInstaller --noconfirm --clean --windowed --onefile \
  --name ThinkTrellis --add-data "assets:assets" main.py

echo
echo "Pronto: dist/ThinkTrellis"
