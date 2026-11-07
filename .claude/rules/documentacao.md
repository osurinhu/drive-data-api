---
paths:
  - "documentacao/**/*.md"
  - "README.md"
---

# Documentação

A documentação é a fonte de verdade do projeto e de `.claude/`; mantê-la exata faz parte da tarefa.

- Onde cada coisa mora: `README.md` é o runbook (operar); `01-arquitetura.md`, estrutura, camadas
  e regras; `02-decisoes.md`, decisões; `03-como-estender.md`, receitas e checklist de revisão;
  `documentacao/README.md`, o índice.
- Decisão nova em `02-decisoes.md` no formato da casa: **a escolha**, *por quê* e o que foi
  **Descartado** (e, quando houver, **Custo aceito** ou **Rede de segurança**).
- Ponto de extensão novo: receita em `03`, linha na tabela "Quero… / Vá para" e item no
  checklist, se for verificável.
- Arquivo novo em `app/`: atualize o "Mapa de arquivos" de `01-arquitetura.md` (e a "Estrutura"
  do README, se for de primeiro nível).
- Contagens escritas por extenso precisam continuar verdadeiras ("Dez receitas", "Dez arquivos",
  "cinco lugares", "quatro papéis").
- Exemplos de código espelham o código real. O ruff ignora `*.md`: ninguém os verifica por você.
- pt-BR, frases diretas, tabela quando houver comparação. Nada que o código não cumpra.
- Renomeou ou reordenou uma seção? Rode `grep -rn "como-estender\|02-decisoes\|01-arquitetura" .claude/`
  e ajuste as referências.
