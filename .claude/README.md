# `.claude/` — o Claude Code neste projeto

Esta pasta transforma `documentacao/` em processo de trabalho: o que ler em cada momento, como
verificar e como revisar. Ela **não repete** a documentação. Aponta para a seção certa e
acrescenta só o que a documentação não diz: o ambiente (Windows + Docker), a ordem dos passos e
a verificação.

```
orientar  →  implementar  →  verificar  →  revisar  →  documentar
CLAUDE.md    /nova-entidade   /verificar    revisor-      rules/documentacao.md
+ rules/     /migracao        /smoke-api    arquitetura
+ hook
```

## Mapa

| Arquivo | Para quê | Carrega quando |
|---|---|---|
| `CLAUDE.md` | fontes de verdade, invariantes, armadilhas do ambiente, fluxo | toda sessão |
| `rules/rotas.md`, `servicos.md`, `repositorios.md`, `schemas.md`, `modelos-e-migracoes.md`, `autorizacao.md`, `extensoes-e-config.md`, `testes.md` | o que cada camada pode e não pode, com a seção da doc que explica o porquê | ao ler ou editar um arquivo da camada (`paths`) |
| `rules/infra.md`, `documentacao.md`, `pasta-claude.md` | Docker, CI e dependências; manutenção da doc; manutenção desta pasta | idem |
| `skills/verificar/` (`SKILL.md` + `verificar.sh`) | `/verificar`: as etapas do CI, no Docker | `/verificar`, ou quando o Claude for testar |
| `skills/migracao/` | `/migracao`: gerar, revisar e validar migração Alembic | `/migracao`, ou ao mudar modelo |
| `skills/nova-entidade/` | `/nova-entidade`: receita 1 ponta a ponta | `/nova-entidade`, ou ao pedir entidade nova |
| `skills/smoke-api/` (`SKILL.md` + `smoke.sh`) | `/smoke-api`: a imagem de produção num container avulso com banco descartável, conferida por HTTP | `/smoke-api`, ou ao pedir para rodar, subir ou testar a API |
| `skills/medir-consultas/` | `/medir-consultas`: conta o SQL de uma operação e detecta N+1 (modelo de medição + modo `medir` do `verificar.sh`) | `/medir-consultas`, ou antes de otimizar consulta |
| `agents/revisor-arquitetura.md` | revisão independente do diff, só leitura | ao fim de mudança não trivial |
| `hooks/estado-docker.sh` | uma linha sobre o estado do Docker, do `db` e do `api` | início, retomada, `/clear` e compactação da sessão (hook `SessionStart`) |
| `settings.json` | permissões e hooks do projeto (versionado) | toda sessão |

Preferências pessoais vão em `settings.local.json`, que não é versionado.

## Princípios

1. **`documentacao/` é a fonte de verdade.** Aqui entram processo, ambiente e ponteiros; conteúdo
   copiado envelheceria em dois lugares.
2. **Contexto sob demanda.** `CLAUDE.md` curto; regras só carregam com a camada; skills só quando
   invocadas; a revisão roda em contexto isolado.
3. **Toda skill termina em `/verificar`**, e mudança não trivial termina no revisor.
4. **Comandos são skills.** Cada skill já é um `/comando`; não há `.claude/commands/`.
5. **Corrigir no uso.** Instrução errada ou incompleta é corrigida na sessão em que aparece e
   registrada abaixo.

## Roadmap

| Onda | Item | Estado |
|---|---|---|
| 1 | `CLAUDE.md`, 11 regras, `settings.json` (nome em minúsculas, permissões, `.env` protegido), `.gitattributes` | feito em 2026-11-06 |
| 1 | `/verificar` | feito e validado: `tudo` (61 testes), `estatico`, `testes`, falha de teste, falha de formatação com diff, limpeza do banco descartável |
| 1 | `/migracao` | feito; validados o rebuild do `api` e o `alembic check`. Falta exercitar com mudança real de modelo |
| 1 | `/nova-entidade` | feito; validar e ajustar no primeiro uso real |
| 1 | `revisor-arquitetura` | feito e validado num ensaio sobre o molde `Item`: zero bloqueantes; os trechos das regras que gerariam falso positivo foram corrigidos (ver registro) |
| 2 | `/smoke-api`: a imagem de produção num container avulso, com banco descartável, um usuário por papel (`flask create-user`), a matriz 401/403/404/409/422 por curl e o `X-Request-ID` rastreado no log — também é a skill que o `/run` embutido procura | feito em 2026-11-06 e validado: 24 verificações em ~30 s, `db` e `api` de desenvolvimento intocados, caminho de falha conferido por mutação. Falta a permissão no `settings.json` (ver registro) |
| 2 | Hook `SessionStart` com uma linha de estado do Docker (daemon, `db`, `api`) | script feito e validado em 4 cenários; **falta registrá-lo no `settings.json`** (ver registro) |
| 2 | `/remover-exemplo`: tirar `Item` com segurança quando houver entidades reais (a migração 0001 cria `users` e `items` juntas; `test_write_cost`, `test_authz` e `test_contracts` usam `Item`) | a fazer, quando existir a primeira entidade real: antes disso não há como validá-lo |
| 3 | `/medir-consultas` (padrão `sql_log` / `test_write_cost`) antes de otimizar | feito em 2026-11-06 e validado: referência de `Item` medida; N+1 provocado por mutação (23 SELECT) e remédio com `selectinload` medido (4); trava contra a medição esquecida conferida |
| 3 | `memory: project` no revisor, para acumular erros recorrentes | avaliado em 2026-11-06: adiado. Duas revisões ainda não mostram erro recorrente, e a memória daria ao revisor escrita no repositório e o viés das revisões anteriores. Reavaliar quando o mesmo achado se repetir em revisões diferentes |
| 3 | Calibrar permissões com `/fewer-permission-prompts` depois de algumas sessões | a fazer; mexe no `settings.json`, então fica com o usuário, fora do modo automático |

## Pendências encontradas no projeto

As cinco que o ensaio do revisor encontrou foram corrigidas (ver registro). Fica uma sugestão,
que muda uma regra de projeto e por isso é decisão do usuário:

| Onde | O quê |
|---|---|
| `tests/test_contracts.py` | Nenhum contrato pega um método de escrita sem o seu `@permission_required` quando a classe tem o piso de leitura: só o teste de 403 daquela operação pega, e ele precisa ser escrito em cada entidade. Um contrato que exigisse, em toda rota protegida de escrita (POST, PUT, PATCH, DELETE), uma permissão que o `leitor` não tem pegaria em todas — mas troca "o piso da classe basta" por uma convenção mais rígida. |

## Registro de ajustes

- 2026-11-06 — Onda 1 criada e validada.
- 2026-11-06 — Ensaio do revisor sobre `Item`. Corrigidos nas regras e no prompt: o 422 no
  `@error_responses`, o piso de leitura × decorator de escrita (com teste de 403 por operação), o
  uso de `http_errors` pelas rotas, a docstring como `summary` público e a precedência do molde
  sobre a doc. O ensaio também afirmou que o `summary` do GET sai cortado; conferido no
  `openapi.json`, é falso — o smorest usa o 1º parágrafo inteiro.
- 2026-11-06 — Skills e agente apareceram na mesma sessão, sem `/reload-skills` nem reinício.
- 2026-11-06 — Corrigidas as cinco pendências do ensaio:
  - teste de 403 do `leitor` no PATCH e no DELETE. A prova foi por mutação: sem o decorator de
    escrita, só esse teste falha, e o `test_contracts.py` continua passando;
  - receita do `Product` (§1.1, §1.5 e §1.10), §7, §10.1 e checklist de `03-como-estender`;
  - exceções de extensão e `http_errors` nas regras de dependência de `01-arquitetura`;
  - a nota interna do POST de `item.py` passou para comentário.

  As regras deixaram de apontar divergências, e `rules/rotas.md` ganhou a exceção prevista do
  `health.py`.
- 2026-11-06 — Primeiro uso real do `revisor-arquitetura`, sobre o diff acima. Não houve
  bloqueante, e o achado importante procedia: a seção "Segurança das rotas" de `01` ainda dizia
  que esquecer o decorator fecha a rota e que o contrato reprova, o que não vale para a escrita.
  Foram corrigidos esse achado e as sugestões: "ressalva" no lugar de "exceção" na regra 1, "na
  criação" × "escritas sobre registro existente", o teste de 403 na §2 e o cabeçalho de
  `test_authz.py`.
- 2026-11-06 — Onda 2: `/smoke-api` e hook `SessionStart`.
  - **O smoke mudou em relação ao roadmap:** não recria o `api` nem usa o banco de
    desenvolvimento. Roda num container avulso com banco descartável, apontado pela interpolação
    do compose (`POSTGRES_DB=<nome>_smoke`), sem ler o `.env`. Assim não suja os dados do usuário
    e ainda prova as migrações do zero pelo entrypoint de verdade.
  - **`docker info` lento:** levou 6 s a frio (1,7 s quente) nesta máquina. O hook e o
    `verificar.sh` passaram a usar `docker version` (~0,5 s).
  - **Hook na forma shell:** no Windows, a forma exec exige `.exe`, e o PowerShell e o cmd também
    enxergam o `bash.exe` do WSL.
  - **`settings.json` pendente:** o modo automático recusou que o Claude o editasse. Faltam o
    registro do hook e a permissão `Bash(bash .claude/skills/smoke-api/smoke.sh:*)`; o trecho
    ficou com o usuário.
  - **Fim de linha:** `grep -c $'\r'` no Bash daqui conta todas as linhas. Confira com
    `git ls-files --eol`.
  - **Mutação reconstrói a imagem:** a mutação usada para validar o smoke reconstruiu a imagem
    `api` com o código mutado. Rodar o smoke de novo depois de restaurar a refez — a mesma
    armadilha vale para qualquer mutação.
- 2026-11-06 — Onda 3: `/medir-consultas`.
  - **Modo `medir` no `verificar.sh`:** no modo `testes`, um pytest que passa mostra só a última
    linha, e o relatório da medição se perderia. O modo `tudo` recusa rodar com
    `tests/test_zz_medir.py` presente, para a medição temporária nunca chegar ao commit.
  - **Medido, não suposto:** com um dono só, o N+1 de muitos-para-um não aparece, porque o dono
    já está na sessão. Com 20 donos foram 23 SELECT; com `selectinload`, 4 constantes (1 a mais
    quando há um dono só). A regra de repositórios passou a pedir donos diferentes na medição.
