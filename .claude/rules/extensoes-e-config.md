---
paths:
  - "app/ext/**/*.py"
  - "app/app.py"
  - "app/http_errors.py"
  - "app/request_context.py"
  - "settings.toml"
  - ".env.example"
---

# Extensões e configuração (`app/ext/`, `settings.toml`)

Infraestrutura fora do caminho da requisição. Receitas: 03-como-estender §7, §8 e §9.

- `app/app.py` não se edita: `create_app()` lê `EXTENSIONS` e `API_V1_ROUTES` do `settings.toml`.
- Cada módulo de `ext/` expõe **exatamente uma** `init_app(app)` e entra em `EXTENSIONS`. A ordem
  é a de inicialização: `ext.observability` primeiro (log com request id), `ext.api` por último
  (registra as rotas). `ext.config` não entra na lista: o factory já o chama.
- Extensão não importa extensão. A ressalva de 01-arquitetura ("Regras de dependência"):
  `ext.config` (o carregador `load_routes`, usado por `ext.api`) e `ext.database` (a instância
  `db`, usada por repositórios e por `ext.commands`) podem ser importadas. Algo novo que duas
  extensões precisem vira módulo neutro na raiz de `app/`, como `http_errors.py` — que não é
  folha (importa `service.errors`) e é usado só por extensões e rotas. `authz.py` e
  `request_context.py` são folhas.
- `ERROR_STATUS` (`http_errors.py`) é o único mapa exceção → status; `STATUS_DESCRIPTION` alimenta
  a mensagem padrão e o OpenAPI. O handler genérico não engole `HTTPException`.
- Configuração não sensível vai para `settings.toml` (`[default]`). Sensível vai para variável
  `FLASK_*`, documentada em `.env.example` com valor de exemplo, nunca real. Leitura:
  `app.config[...]` no `init_app`, `current_app.config[...]` na requisição. Se for obrigatória,
  falhe na inicialização com mensagem explícita, como `ext/database.py` e `ext/auth.py`.
- Constante validada por schema (ex.: limites de paginação) fica com o schema, não na configuração.
- Comando CLI (`ext/commands.py`): `@click.command` + `@with_appcontext`; serviços saem de
  `current_app.container.x_service()` (o comando não toca repositório nem sessão); erro de domínio
  vira `click.ClickException(str(erro))`; acrescente o comando à tupla do `init_app`.
- Container (`ext/container.py`): repositório e serviço novos são dois `providers.Factory`.
