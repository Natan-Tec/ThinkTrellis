@echo off
setlocal
title Empacotando ThinkTrellis

echo Instalando a ferramenta de empacotamento, se necessario...
py -m pip install -r requirements-build.txt
if errorlevel 1 goto :error

echo Criando ThinkTrellis.exe...
py -m PyInstaller --noconfirm --clean --windowed --onefile --name ThinkTrellis --icon "assets\thinktrellis.ico" --add-data "assets;assets" main.py
if errorlevel 1 goto :error

echo.
echo Pronto: dist\ThinkTrellis.exe
pause
exit /b 0

:error
echo.
echo Nao foi possivel gerar o executavel. Confira se o Python esta instalado.
pause
exit /b 1
