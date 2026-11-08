---
paths:
  - "tests/**/*.py"
---

# Testes (`tests/`)

pytest contra PostgreSQL, MongoDB e RabbitMQ reais, nunca SQLite nem um Mongo ou broker em
memória. Dublê só em `tests/unit/` ou para provocar uma falha, como a sonda derrubada do
`test_health.py`. Rode com `/verificar testes <args>`. Moldes: `tests/test_vehicle.py` (CRUD) e
`tests/test_authz.py` (papéis). Receita: 03-como-estender §1.10.

- A pasta diz a infraestrutura do teste. A raiz usa Postgres e HTTP, `tests/unit/` não usa
  nenhuma, `tests/mongo/` e `tests/rabbitmq/` usam o serviço do nome, e `tests/e2e/` usa os três.
  O `conftest.py` de cada pasta anula o `_clean_tables` (quem não usa Postgres) e tem a limpeza
  autouse da sua infraestrutura. Fixture que serve a mais de uma pasta fica no `conftest.py` da
  raiz, sem autouse, e não puxa infraestrutura de outra pasta: o `mongo_db` vem de uma aplicação
  mínima, e não do `app`, que recria as tabelas do Postgres. Nome de arquivo de teste é único na
  suíte inteira: sem `__init__.py`, o pytest recusa dois `test_x.py`.
- Fixtures (`tests/conftest.py`): `client`; `login(name, password, role)` → `(user_dict, headers)`;
  `headers` (usuário comum já autenticado); `make_user(...)` → dict; `set_role(nome, papel)` troca o
  papel sem reemitir o token; `sql_log` (lista do SQL emitido — chame `.clear()` depois do preparo);
  `mongo_db` (o banco `<MONGO_DATABASE>_test`, para ler e preparar; não abre contexto de
  aplicação); `bronze_limpo` (apaga as coleções no fim; autouse em `tests/mongo/` e
  `tests/e2e/`); `amqp_url`, `amqp_management_url`, `amqp_management` e `amqp_vhost` (o vhost
  `drive-data-test`, criado pela API de gerenciamento); `delivery_limit`; `queue_max_bytes`; `amqp_topology` (a
  topologia aplicada nele por `topology.apply`); `bronze_queue` e `dead_letters` (a fila e a
  DLQ vazias no começo e no fim; chamadas, devolvem e tiram as mensagens como
  `(propriedades, corpo)`); `publicar(corpo)` (publica no exchange da telemetria, com
  confirmação); `novo_lote(registros)` (um `TelemetryBatchV1` válido); `fila_em_memoria` (o
  dublê `FilaEmMemoria` do `TelemetryPublisher`, com `publicados` e `falhar`);
  `observador` (dublê de qualquer porta de observação, de `tests/dubles.py`, com `nomes()`,
  `de(nome)` e `chamadas`); `leitura_em_memoria` (o dublê `LeituraEmMemoria` do `BronzeReader`, com `agora`,
  `gravar(ingerido_ha)`, `fora` e `chamadas`). Em `tests/mongo/`, `mongo_avulso(nome, uri)`
  abre um Mongo em outro endereço (fora do ar, credencial errada).
- A rota usa o dublê por `app.container.<provider>.override(providers.Object(dublê))`, com
  `reset_override()` na saída (fixtures `fila` de `tests/test_telemetry.py` e `bronze` de
  `tests/test_bronze_api.py`). O e2e troca só a configuração de um serviço, sobrepondo o
  provider dele com um `providers.Factory` novo (a fixture `sem_janela` de
  `tests/e2e/test_pipeline.py`). O adaptador contra
  o broker de verdade fica para `tests/rabbitmq/` e `tests/e2e/`; na raiz, só apontado para uma
  porta fechada (o 503 com o broker fora). Dado de teste fixo (o exemplo canônico do
  contrato) mora em `tests/data/`.
- As tabelas são esvaziadas após cada teste, e em `tests/mongo/` e `tests/e2e/` também as
  coleções: a ordem não importa, mas não presuma ids.
- Fixture nunca deixa um contexto de aplicação aberto que o teste não pediu: o `with_appcontext`
  da CLI reaproveita o contexto que encontra, e o comando rodaria contra a aplicação errada. O
  contexto que os adaptadores de `repository/mongo/` exigem é aberto pelo conftest de
  `tests/mongo/`, só ali. No e2e, quem usa o adaptador abre `with app.app_context()`.
- O banco de testes nasce de `create_all()`, não das migrações: teste verde não prova que a
  migração existe. Por isso o `/verificar` também roda o `alembic check`.
- Nome do teste em pt-BR descrevendo o comportamento
  (`test_nome_duplicado_para_o_mesmo_dono_retorna_409`); docstring quando o porquê não for óbvio.
  Preparo, ação e verificação separados por linha em branco.
- Parâmetro de fixture não usado reprova no ruff (`ARG001`).
- Cobertura mínima de recurso novo: criação, listagem paginada, 409 de duplicado, 422 de entrada
  inválida, exclusão lógica sumindo das consultas, **404 para recurso de outro dono** e **403 com
  `Role.SILVER` em cada operação de escrita** — na criação, `login(role=Role.SILVER)`; nas escritas
  sobre registro existente (PATCH, DELETE, ações como o `arquivar` da §2), crie como `usuario`,
  rebaixe com `set_role(nome, Role.SILVER)` e espere 403. Esse 403 sai do piso da classe; a falta
  do decorator o `test_contracts.py` pega, e quem prova que a permissão da escrita é a certa é o
  teste do papel que só lê (`monkeypatch.setitem` em `ROLE_PERMISSIONS`, molde em `test_authz.py`).
- `test_contracts.py` guarda contratos que nenhum teste funcional pegaria. Nunca o afrouxe para
  passar: corrija a causa. Só altere `ROTAS_PUBLICAS` ao abrir uma rota de propósito.
- Sub-recurso: também o 404 do pai de outro dono em **cada** operação do filho, um vínculo de
  outro par (outro pai, outra pessoa) que nenhuma operação alcança, nem a da outra ponta, e o
  filho que some com o pai excluído (`tests/test_vehicle_driver.py`).
- `test_write_cost.py` e `test_read_cost.py` fixam contagens medidas; mudar um número exige
  medir (`/medir-consultas`) e explicar no cabeçalho. `tests/test_zz_medir.py` é a medição
  temporária daquela skill: nunca a deixe no commit, e o `/verificar` (tudo) recusa rodar com
  ela presente.
