---
name: verificar
description: Roda o portão de qualidade do projeto dentro do Docker, com as mesmas etapas do CI — ruff check, ruff format --check, mypy, ciclo de migrações Alembic num banco descartável e pytest contra PostgreSQL, MongoDB e RabbitMQ reais. Use depois de qualquer mudança de código e antes de declarar uma tarefa pronta, e sempre que for rodar testes, lint, checagem de tipos ou conferir migrações (no host não há pytest, ruff, mypy nem alembic).
argument-hint: "[tudo | estatico | testes <args do pytest> | migracoes | medir]"
allowed-tools: Bash(bash .claude/skills/verificar/verificar.sh *)
---

# /verificar

Rode a partir da raiz do repositório:

```bash
bash .claude/skills/verificar/verificar.sh $ARGUMENTS
```

Sem argumento, o modo é `tudo`.

| Modo | Etapas | Quando usar |
|---|---|---|
| `tudo` | ruff check, ruff format --check, mypy, migrações, pytest | antes de concluir qualquer tarefa (~1 min) |
| `estatico` | ruff check, ruff format --check, mypy | mexeu em código e não precisa de banco |
| `testes [args]` | só o pytest, com os argumentos repassados | ciclo curto: `testes tests/test_item.py -k conflito` |
| `migracoes` | upgrade head → check → downgrade base num banco descartável | criou ou editou migração |
| `medir [args]` | só `tests/test_zz_medir.py`, com a saída inteira mesmo quando passa | medição de consultas do `/medir-consultas` |

Com `tests/test_zz_medir.py` presente, o `tudo` recusa rodar: a medição é temporária e não pode
chegar ao commit.

O script já resolve as armadilhas do ambiente: reconstrói a imagem `test` (o código é copiado
para ela — sem rebuild, testa-se a versão antiga), usa `-T` e `MSYS_NO_PATHCONV`, e cria e
apaga o banco `<POSTGRES_DB>_mig` pelo próprio container `db`, sem ler o `.env`.

## Como ler a saída

- Uma linha por etapa: `OK  etapa (Ns) — última linha da saída` ou `FALHOU  etapa`, seguida do
  fim da saída dela.
- A última linha é `RESULTADO: OK` ou `RESULTADO: FALHOU — <etapas>`.
- Código 2 com `INFRA: ...` = Docker parado, build quebrado ou banco fora do ar. Não é falha do
  código: resolva o ambiente (Docker Desktop fechado → peça ao usuário para abrir) e rode de novo.
- `INFRA` com `dependency failed to start: container ... is unhealthy`: o `test` espera o `db`,
  o `mongo` e o `rabbitmq` saudáveis, e o compose recusa subir na hora se um estiver
  `unhealthy`. Veja qual em `docker compose ps`. Se o serviço está de pé
  (`docker compose logs --tail 20 <serviço>`), foi o healthcheck que estourou o prazo sob
  carga: ele volta sozinho no ciclo seguinte (30 s). Confira com
  `docker inspect <container> --format '{{json .State.Health}}'` e rode de novo.

## O que fazer quando uma etapa falha

- **ruff check** — corrija à mão; o `ruff check --fix` é bloqueado nas permissões de propósito.
  Parâmetro de fixture não usado reprova (ARG001): tire-o da assinatura do teste.
- **ruff format** — o script mostra o diff; aplique-o com Edit. Formatar dentro do container não
  altera os arquivos do host.
- **mypy** — cobre só `service`, `repository`, `domain` e `schemas` (ver `pyproject.toml`). A
  tipagem é contrato nessas camadas: não silencie com `# type: ignore` sem motivo escrito.
- **alembic** — `check` acusou operações novas → modelo mudou sem migração: use `/migracao`.
  `downgrade` falhou → o `downgrade()` não espelha o `upgrade()`.
- **pytest em `tests/test_contracts.py`** — quase sempre é um passo de receita esquecido; a
  docstring do teste que falhou diz qual (exceção fora do `ERROR_STATUS`, permissão sem papel,
  rota pública não declarada, operação sem permissão). Corrija a causa, nunca o teste.
- **pytest em `tests/test_write_cost.py`** — o número de consultas mudou. Não atualize o número
  sem entender o porquê: ver "Sem `refresh()` depois do `flush()`" em `documentacao/02-decisoes.md`.
  Para ver os comandos emitidos, use o `/medir-consultas`.

## Regra de uso

Itere com `testes <arquivo>`; antes de dizer que terminou, rode `tudo` e informe o resultado,
com as falhas se houver. Não declare pronto com etapa falhando.
