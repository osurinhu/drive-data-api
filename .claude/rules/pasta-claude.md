---
paths:
  - ".claude/**"
---

# Manutenção da pasta `.claude/`

Mapa, roadmap e registro de ajustes: `.claude/README.md`.

- `documentacao/` é a fonte de verdade: aqui entram processo, ambiente e ponteiros para a seção
  certa. Não copie conteúdo da documentação — ele envelheceria em dois lugares.
- `CLAUDE.md` carrega em toda sessão: mantenha-o curto. O que vale para uma camada vai para
  `rules/` com `paths`; procedimento de vários passos vira skill.
- Toda skill termina em verificação (`/verificar`); fluxo que muda código não trivial termina no
  agente `revisor-arquitetura`.
- Skill nova: `.claude/skills/<nome>/SKILL.md` com `description` dizendo quando usar. Script fica na
  pasta da skill, em LF, chamado por caminho relativo à raiz — é o que a permissão do
  `settings.json` casa.
- "Comandos" são skills (`/nome`): não crie `.claude/commands/`.
- Hook: o script fica em `.claude/hooks/`, em LF, e é registrado no `settings.json` na forma shell
  (`bash "$CLAUDE_PROJECT_DIR/.claude/hooks/<nome>.sh"`). No Windows, a forma exec exige `.exe`, e
  o PowerShell e o cmd também enxergam o `bash.exe` do WSL. A saída do `SessionStart` vira
  contexto: deve ser curta e sempre sair com 0. Meça o tempo, porque hook lento atrasa toda
  sessão (o `docker info` chegou a 6 s; o `docker version`, ~0,5 s).
- O modo automático recusa que o Claude edite o `.claude/settings.json` (permissões e hooks):
  proponha o trecho exato e deixe o usuário aplicar.
- Fim de linha se confere com `git ls-files --eol <arquivo>`. No Bash daqui, o `grep -c $'\r'`
  conta todas as linhas, não só as com CR.
- Na versão 2.1.319, skills e agentes novos apareceram sozinhos, na mesma sessão (a doc oficial
  fala em `/reload-skills` e reinício). Se não aparecerem, use um dos dois. `CLAUDE.md` e `rules/`
  novos só valem a partir da sessão seguinte.
- Instrução que se mostrou errada numa sessão: corrija na hora e registre uma linha no
  "Registro de ajustes" do README.
