---
paths:
  - "app/ext/**/*.py"
  - "app/app.py"
  - "app/blueprint/http_errors.py"
  - "app/instrumentation/**/*.py"
  - "settings.toml"
  - ".env.example"
---

# Extensões e configuração (`app/ext/`, `settings.toml`)

Infraestrutura fora do caminho da requisição. Receitas: 03-como-estender §7, §8 e §9.

- `app/app.py` não se edita: `create_app()` lê `EXTENSIONS` e `API_V1_ROUTES` do `settings.toml`.
- Cada módulo de `ext/` expõe **exatamente uma** `init_app(app)` e entra em `EXTENSIONS` do
  `[default]` **e** do perfil de cada processo que o usa (`[api]`, `[bronze_api]`, `[consumer]`,
  `[setup]`); o `tests/unit/test_composicao.py` reprova se o `[default]` deixar de ser a união
  dos perfis. A ordem é a de inicialização: `ext.observability` primeiro (log com request id),
  `ext.api` por último (registra as rotas). `ext.config` não entra na lista: o factory já o chama.
- Extensão que conecta serviço externo registra a sonda do health com `register_probe`
  (`instrumentation.probes`). A sonda devolve `bool`, nunca levanta exceção, loga o motivo e
  desiste em `HEALTH_PROBE_TIMEOUT_SECONDS`. O nome dela ganha campo no `HealthSchema` (o
  `test_contracts.py` reprova se faltar), e ser crítica ou não é decisão por perfil
  (`HEALTH_CRITICAL`). Cliente com pool ou threads não conecta na subida e é recriado no
  processo que não o criou, depois de um fork (molde: `ext/mongo.py`, com `connect=False`).
  Mensagem de configuração inválida nunca repete a URL ou a URI, nem em pedaços: ela leva a
  senha. Use mensagem fixa com `from None`, porque o erro da biblioteca repete trechos dela.
- Extensão não importa extensão. A ressalva de 01-arquitetura ("Regras de dependência"):
  `ext.config` (o carregador `load_routes`, usado por `ext.api`), `ext.database` (a instância
  `db`, usada por repositórios e por `ext.commands`) e `ext.mongo` (o cliente, usado pelos
  adaptadores de `repository/mongo/`) podem ser importadas. O `ext.mongo` importa de volta só o
  `repository.mongo.indexes` e o `repository.mongo.users`, que não importam `ext`. Algo novo que duas
  extensões precisem vira módulo neutro fora de `ext/`, como `instrumentation/` (que guarda o
  `correlation.py`) ou o `blueprint/http_errors.py` — que não é folha (importa
  `service.errors`) e é usado só por extensões e rotas. Ele nunca importa `ext`, e o
  `blueprint/__init__.py` fica sem import: a rota importa `ext.auth`, que importa
  `blueprint.http_errors`, e qualquer um dos dois fecharia o ciclo. Vocabulário puro vira folha
  do domínio (`rules/dominio.md`). Nada novo fica solto na raiz de `app/`: lá só mora o
  `app.py`.
- `ERROR_STATUS` (`blueprint/http_errors.py`) é o único mapa exceção → status; `STATUS_DESCRIPTION` alimenta
  a mensagem padrão e o OpenAPI. O handler genérico não engole `HTTPException`.
- Configuração não sensível vai para `settings.toml` (`[default]`). Sensível vai para variável
  `FLASK_*`, documentada em `.env.example` com valor de exemplo, nunca real. Leitura:
  `app.config[...]` no `init_app`, `current_app.config[...]` na requisição. Se for obrigatória,
  falhe na inicialização com mensagem explícita, como `ext/database.py` e `ext/auth.py`.
- Valor diferente por processo: repita a chave na seção do perfil (03-como-estender §9). Uma
  lista no perfil substitui a do `[default]` inteira. Perfil sem rota declara
  `HEALTH_CRITICAL = []`: herdado, o `["database"]` do `[default]` reprovaria no
  `test_composicao.py`. `FLASK_PROCESS` escolhe o perfil, e um nome que não é seção
  reprova a subida. Na suíte, nunca monte a aplicação de outro perfil (o `api` e o `v1` de
  `ext.api` são globais): leia a configuração com `build_config()` ou suba um processo à parte,
  como no `test_composicao.py`.
- Constante validada por schema (ex.: limites de paginação) fica com o schema, não na configuração.
- Comando CLI (`ext/commands.py`): `@click.command` + `@with_appcontext`; serviços saem de
  `current_app.container.x_service()` (o comando não toca repositório nem sessão); erro de domínio
  vira `click.ClickException(str(erro))`; acrescente o comando à tupla do `init_app`. Exceção:
  o comando de operação de uma infraestrutura (`flask rabbitmq setup`, `flask mongo setup`)
  fica na extensão dona dela, num `AppGroup` com o nome do módulo, registrado no `init_app`
  (03-como-estender §8): o perfil `[setup]` não carrega o `ext.commands`, que traz banco e
  container. O processo de longa duração (`flask consumer run`) tem a extensão dele
  (`ext/consumer.py`), que só o perfil `[consumer]` carrega.
- Container (`ext/container.py`): repositório e serviço novos são dois `providers.Factory`.
  Adaptador com conexão (o publisher) é `providers.Singleton`, e o que ele recebe do
  `settings.toml` vem de `config` (`providers.Configuration`), preenchido no `init_app`.
- O consumer tem o container dele (`ext/consumer_container.py`, `app.consumer_container`), só
  com o lado do consumer: nada do `ext/container.py` (SQL, publisher, serviços de cadastro)
  entra ali, e o `test_fronteiras.py` reprova se entrar. O consumidor da fila é `Factory`: cada
  `flask consumer run` é um processo, e a conexão é do `run`.
