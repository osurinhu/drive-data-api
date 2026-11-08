# flask-api-base

Base para APIs REST: Flask 3.1 + flask-smorest (OpenAPI) + SQLAlchemy 2 + PostgreSQL 16 +
Alembic, com injeção de dependência (`dependency-injector`), JWT e autorização por permissões.
Responda em pt-BR. Docstrings, comentários, mensagens de erro, nomes de teste e documentação
são em pt-BR; os métodos herdados das classes base são em inglês (`get_by_id`, `before_create`).
Siga o idioma do arquivo vizinho.

## Fonte de verdade: `documentacao/`

- `documentacao/01-arquitetura.md` — camadas e o que cada uma **não pode** fazer. Antes de mudar estrutura.
- `documentacao/02-decisoes.md` — o porquê de cada escolha e o que foi descartado. Antes de
  "simplificar" algo que parece estranho, procure aqui: quase sempre é deliberado.
- `documentacao/03-como-estender.md` — receitas (entidade, endpoint, consulta, regra, erro, campo,
  extensão, comando CLI, configuração, autorização, contrato da fila, bronze, sub-recurso) e o
  checklist de revisão. Toda implementação.
- `README.md` — runbook: subir, testar, migrar, configurar.

Molde real: os arquivos de `Vehicle` (modelo, repositório, serviço, schemas, rota, testes) — os
trechos da documentação são resumidos. As regras de cada camada estão em `.claude/rules/` e
carregam sozinhas quando você lê ou edita arquivos daquela camada.

## Invariantes que mais quebram

1. Rota → serviço → repositório → modelo; a seta nunca aponta para cima.
2. Estender é aditivo: módulo novo + uma linha em `EXTENSIONS` ou `API_V1_ROUTES` do
   `settings.toml`, no `[default]` e no perfil de cada processo que o usa (`FLASK_PROCESS`).
   Nunca editar `app/app.py`.
3. Imports a partir de `app/`: `from ext.auth import ...`, nunca `from app.ext...`.
4. Autorização é declarada: a rota exige **permissão** (nunca papel, nunca `if`). Escopo de dono
   explícito em toda chamada de serviço, sem default. Sem permissão = 403; recurso de outro dono = 404.
5. Erro de domínio = exceção em `app/service/errors.py` + linha em `ERROR_STATUS`
   (`app/blueprint/http_errors.py`). Rota não tem `try/except`.
6. O schema é a única fonte da saída: campo fora do schema não aparece na API.
7. Segredo só via variável `FLASK_*` / `.env`, nunca em arquivo versionado. Nunca exiba o `.env`.
8. A fila é a única ligação entre producer (`api`) e consumer: do pipeline, os dois só
   compartilham as folhas do domínio (contrato, portas) e a conexão com o broker. O consumer não alcança o lado HTTP nem o SQL, e o producer
   não alcança o Mongo (`tests/unit/test_fronteiras.py`). O consumer só dá ack depois de gravar.

## Ambiente (Windows + Git Bash + Docker)

- No host não há pytest/ruff/mypy/alembic (o Python local é 3.13; o projeto, 3.12): tudo roda
  nos containers. Para verificar, use `/verificar`.
- `docker compose run test` sem rebuild testa código velho: o código é copiado para a imagem.
  O `/verificar` já reconstrói.
- O `api` roda a imagem do último build. Mudou modelo? `docker compose up -d --build --wait api`
  antes de `alembic revision --autogenerate` (ver `/migracao`).
- Serviços: `db`, `mongo`, `rabbitmq`, o job `setup` (migra, aplica a topologia e os índices e
  termina), `api` (:5000), `bronze-api` (:5001) e `consumer` (sem porta publicada: `flask consumer run`).
  O `test` espera os três primeiros saudáveis.
- `docker compose exec`/`run` sempre com `-T` (o Bash daqui não tem TTY).
- O Git Bash reescreve `/srv` como `C:/Program Files/Git/srv`: use `MSYS_NO_PATHCONV=1` em
  comando docker com caminho de container.
- Python no host: `python -X utf8` (o console é cp1252 e quebra com acentos e setas).

## Fluxo de trabalho

1. Ache a receita em `documentacao/03-como-estender.md` e o molde em `Vehicle`.
2. Implemente seguindo as regras da camada.
3. `/verificar testes <arquivo>` para iterar; `/verificar` (tudo) antes de concluir. Mudou
   rota, autenticação, configuração, Docker ou migração? Rode também o `/smoke-api`. Mudou o
   caminho do pipeline (telemetria, fila, consumer, bronze)? Também o `/smoke-pipeline`.
4. Mudança não trivial: peça revisão ao agente `revisor-arquitetura`. Ele cuida da
   conformidade com a arquitetura; para bugs há `/code-review` e para segurança `/security-review`.
5. Mudou comportamento documentado ou tomou decisão estrutural? Atualize `documentacao/` na
   mesma mudança.

`git add`, `commit` e `push` ficam com o usuário (bloqueados em `.claude/settings.json`).

## Ferramentas do projeto

- `/verificar [tudo|estatico|testes <args>|migracoes]` — portão igual ao CI, no Docker.
- `/migracao <descrição>` — gerar, revisar e validar migração Alembic.
- `/nova-entidade <Nome>` — entidade ponta a ponta (receita 1).
- `/smoke-api` — a imagem de produção num container avulso com banco descartável, conferida
  por HTTP (a matriz 401/403/404 e o `X-Request-ID` no log). É também o "rodar/subir a API".
- `/smoke-pipeline` — os três processos reais (`api`, `consumer`, `bronze-api`) num projeto do
  compose à parte, conferidos por HTTP do app à silver (de 7 a 9 min, com o caos).
- `/medir-consultas <operação>` — conta o SQL emitido e detecta N+1 antes de otimizar.
- Agente `revisor-arquitetura` — revisão independente do diff.
- Hook `SessionStart` (`.claude/hooks/estado-docker.sh`) — a sessão começa com uma linha sobre o
  estado do Docker e dos serviços `db`, `mongo`, `rabbitmq`, `api`, `bronze-api` e `consumer`.

Mapa da pasta e próximas ondas: `.claude/README.md`. Se uma instrução de `.claude/` se mostrar
errada ou incompleta, corrija-a na mesma sessão e registre no README.
