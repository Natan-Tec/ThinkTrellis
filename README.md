# ThinkTrellis 1.0

Editor simples de mapas mentais e conceituais para computador. Foi feito para quem está começando: os botões mostram o caminho principal, os atalhos são opcionais e o programa funciona inteiramente offline.

## Recursos

- Escolha entre mapa mental e mapa conceitual ao criar um arquivo.
- Tema claro e escuro.
- Criação, edição, movimentação, cores, notas e organização automática das caixas.
- Relações nomeadas com setas em mapas conceituais.
- Lista de conceitos usando o texto que está nas caixas, sem IDs técnicos.
- Desfazer com botão e `Ctrl+Z`.
- Salvamento em `.thinktrellis`, rascunho recuperável e abertura de arquivos `.nexmind` antigos.
- Exportação em PDF e SVG.
- Manual, notas da versão e créditos no menu Ajuda.
- Logo na interface e ícone próprio no executável Windows.

## Abrir o código

O projeto usa somente Python com Tkinter na execução.

```bash
python main.py
```

É necessário Python 3.10 ou superior com Tkinter disponível. Quem receber o executável não precisa instalar Python, Node.js, Electron nem dependências extras.

## Criar o executável Windows

Em um computador de desenvolvimento com Python, execute:

```bat
build_windows.bat
```

O resultado será `dist\ThinkTrellis.exe`, com a logo aplicada ao executável e à janela.

## Testes

```bash
python -m unittest discover -s tests -v
```

## Créditos

© 2026 Pixel Shell. Todos os direitos reservados.  
Desenvolvido por Natan Gabriel.
