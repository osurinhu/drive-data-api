# `.claude/` — o Claude Code neste projeto

Esta pasta transforma `documentacao/` em processo de trabalho: o que ler em cada momento, como
verificar e como revisar. Ela **não repete** a documentação. Aponta para a seção certa e
acrescenta só o que a documentação não diz: o ambiente (Windows + Docker), a ordem dos passos e
a verificação.

```
orientar  →  implementar  →  verificar  →  revisar  →  documentar
CLAUDE.md    /nova-entidade   /verificar    revisor-      rules/documentacao.md
+ rules/     /migracao        /smoke-api    arquitetura
+ hook                        /smoke-pipeline
```

## Mapa

| Arquivo | Para quê | Carrega quando |
|---|---|---|
| `CLAUDE.md` | fontes de verdade, invariantes, armadilhas do ambiente, fluxo | toda sessão |
| `rules/rotas.md`, `servicos.md`, `servicos-telemetria.md`, `repositorios.md`, `repositorios-mongo.md`, `schemas.md`, `dominio.md`, `contratos.md`, `modelos-e-migracoes.md`, `autorizacao.md`, `extensoes-e-config.md`, `mensageria.md`, `testes.md` | o que cada camada pode e não pode, com a seção da doc que explica o porquê | ao ler ou editar um arquivo da camada (`paths`) |
| `rules/infra.md`, `documentacao.md`, `pasta-claude.md` | Docker, CI e dependências; manutenção da doc; manutenção desta pasta | idem |
| `skills/verificar/` (`SKILL.md` + `verificar.sh`) | `/verificar`: as etapas do CI, no Docker | `/verificar`, ou quando o Claude for testar |
| `skills/migracao/` | `/migracao`: gerar, revisar e validar migração Alembic | `/migracao`, ou ao mudar modelo |
| `skills/nova-entidade/` | `/nova-entidade`: receita 1 ponta a ponta | `/nova-entidade`, ou ao pedir entidade nova |
| `skills/smoke-api/` (`SKILL.md` + `smoke.sh`) | `/smoke-api`: a imagem de produção num container avulso com banco descartável, conferida por HTTP | `/smoke-api`, ou ao pedir para rodar, subir ou testar a API |
| `skills/smoke-pipeline/` (`SKILL.md` + `smoke.sh` + `fluxo.py` + `compose.override.yml`) | `/smoke-pipeline`: os três processos reais num projeto do compose à parte, sem porta no host e com volumes descartáveis, conferidos por HTTP do app à silver | `/smoke-pipeline`, ou ao concluir mudança no caminho do pipeline |
| `skills/medir-consultas/` | `/medir-consultas`: conta o SQL de uma operação e detecta N+1 (modelo de medição + modo `medir` do `verificar.sh`) | `/medir-consultas`, ou antes de otimizar consulta |
| `agents/revisor-arquitetura.md` | revisão independente do diff, só leitura | ao fim de mudança não trivial |
| `hooks/estado-docker.sh` | uma linha sobre o estado do Docker e dos serviços `db`, `mongo`, `rabbitmq`, `api`, `bronze-api` e `consumer` | início, retomada, `/clear` e compactação da sessão (hook `SessionStart`) |
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
| 2 | `/smoke-pipeline`: o compose inteiro num projeto à parte (`-p smoke-pipeline`), na imagem de produção, com um usuário por papel, a matriz da leitura do bronze e o lote do app à silver, sem perda e sem repetição; o caos entra na Fase 5 | feito em 2026-11-07 (passo 1.5) e validado: 18 verificações em ~4 min, sem tocar no ambiente de desenvolvimento. Falta a permissão no `settings.json` (ver registro) |
| 2 | `/remover-exemplo`: tirar `Item` com segurança quando houver entidades reais | feito à mão na Fase 3 do pipeline (2026-11-08, migração 0006). Era operação de uma vez só: a skill não chegou a existir |
| 3 | `/medir-consultas` (padrão `sql_log` / `test_write_cost`) antes de otimizar | feito em 2026-11-06 e validado: referência de `Item` medida; N+1 provocado por mutação (23 SELECT) e remédio com `selectinload` medido (4); trava contra a medição esquecida conferida |
| 3 | `memory: project` no revisor, para acumular erros recorrentes | avaliado em 2026-11-06: adiado. Duas revisões ainda não mostram erro recorrente, e a memória daria ao revisor escrita no repositório e o viés das revisões anteriores. Reavaliar quando o mesmo achado se repetir em revisões diferentes |
| 3 | Calibrar permissões com `/fewer-permission-prompts` depois de algumas sessões | a fazer; mexe no `settings.json`, então fica com o usuário, fora do modo automático |

## Pendências encontradas no projeto

Nenhuma aberta. As cinco que o ensaio do revisor encontrou foram corrigidas (ver registro), e a
sugestão que restava, um contrato para o método de escrita sem `@permission_required`, virou o
`test_toda_escrita_protegida_declara_a_propria_permissao`.

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
- 2026-11-07 — Pipeline de telemetria, passo 1.0, que só move arquivos:
  - os repositórios da base foram para `app/repository/sql/`, a primeira subpasta por tecnologia
    (02-decisoes, "Um nível de pasta por papel, outro por tecnologia");
  - o `docker/initdb/` foi para `docker/postgres/initdb/`;
  - `rules/repositorios.md` passou a valer só para `app/repository/sql/**`, porque o texto dela é
    todo sobre SQL;
  - o `/nova-entidade` aponta para o novo caminho do repositório.
- 2026-11-07 — Pipeline de telemetria, passo 1.1 (infraestrutura mínima): Mongo e RabbitMQ no
  compose e no CI, perfis por `FLASK_PROCESS`, health por dependência e `/health/live`, serviço
  `bronze-api`, pastas de teste por infraestrutura.
  - **Desvios do plano:**
    - o job `setup` veio do passo 1.3, porque com dois processos HTTP a migração na subida já
      disputava o banco;
    - o `prometheus-client` ficou para a Fase 6, quando for usado, porque a base corrigiu
      justamente dependência declarada e nunca usada;
    - a sonda do banco foi para `ext/database.py`, e não ficou no `health.py`: assim a rota não
      importa a extensão de serviço externo nenhum;
    - o `test_composicao.py` mora em `tests/unit/`, porque não usa serviço;
    - `messaging/connection.py` e `messaging/management.py` nasceram já aqui, porque a sonda e
      a suíte precisam deles.
  - **Regras e skills:** `rules/rotas.md` perdeu a exceção do `health.py`, que não trata mais
    exceção nem importa o `db`. `extensoes-e-config.md`, `infra.md` e `testes.md` ganharam
    perfis, sondas, job `setup` e pastas de teste. O `/migracao` passou a citar o `setup` no
    lugar do entrypoint, e o `/verificar` ganhou o caso da dependência `unhealthy`. O hook
    mostra os cinco serviços e levou 1,1 s quente (3,4 s a frio).
  - **RabbitMQ na primeira subida do volume:** o healthcheck como root criou o `.erlang.cookie`
    antes do servidor, que caiu com `eacces`. Ele passou a rodar com `gosu rabbitmq`.
  - **Healthcheck sob carga:** o `mongosh` leva de 1 a 3 s parado e passou de 5 s durante um
    build. O Mongo ficou `unhealthy` sem estar fora, e o `/verificar` saiu com `INFRA`. O
    timeout foi para 20 s no Mongo e no RabbitMQ.
  - **pika:** o `AMQPConnectionError` tem `str()` vazio e o motivo nos `args`, então a sonda
    usa `describe_error` para logar.
  - **Lock:** o cabeçalho do `requirements.lock.txt` passou a instalar o lock atual antes do
    `pip freeze`, para que a dependência nova não atualize as outras.
  - **`.env` do usuário:** o Claude acrescentou `MONGO_ROOT_PASSWORD` e
    `RABBITMQ_ADMIN_PASSWORD`, com valores aleatórios que nunca foram exibidos.
  - **Revisão do `revisor-arquitetura`:** nenhum bloqueante e sete achados importantes,
    corrigidos:
    - uma senha com `/` sem codificação aparecia em pedaços no erro do pika e do PyMongo. As
      mensagens ficaram fixas, com `from None`, e o teste confere o traceback inteiro. O cliente
      do Mongo passou a nascer na subida com `connect=False`, para validar a URI sem conectar;
    - o `[setup]` declarava o Mongo e o RabbitMQ sem usá-los;
    - o wiring do container importava todas as rotas em todo processo e passou a ligar só as do
      perfil;
    - os testes da raiz sondavam o Mongo e o RabbitMQ de verdade e passaram a usar dublês; o
      teste com as três dependências de verdade foi para `tests/e2e/`, e o `mongo_db` deixou de
      depender do Postgres;
    - o `/nova-entidade` não citava os perfis;
    - o `messaging/` estava sem regra e ganhou a `rules/mensageria.md`;
    - o `test_composicao.py` gravava `app/` no `PYTHONPATH`, um sexto lugar do invariante, e
      passou a repassar o `sys.path` da suíte.

    As sugestões também entraram: a redação de "não importa extensão" e de "quatro processos",
    o `HEALTH_CRITICAL = []` dos perfis sem rota, preparo, ação e verificação separados, o
    timeout de 20 s no CI e o aviso de não expor o `/health/` na internet.
- **2026-11-07 — passo 1.2 do pipeline (papel `silver` e `Vehicle`).**
  - **Autorização:** as permissões do plano entraram todas de uma vez (`veiculo:*`,
    `telemetria:enviar`, `bronze:*`), porque o teste "a `silver` fica fora do `admin`" precisa
    das do bronze. A `silver` é um ramo à parte, disjunto do `admin`. A migração 0002 troca o
    CHECK, e o downgrade falha de propósito se ainda houver conta `silver`.
  - **`Vehicle`:** o VIN é único entre os ativos de qualquer dono (`uq_vehicles_vin_active`,
    migração 0003). O formato é ISO 3779 sem o dígito verificador (`vin.py`), e o VIN é imutável:
    o PATCH só aceita o apelido, e o serviço recusa a troca.
  - **Desvio do plano:** o `ports.py` nasceu aqui, um passo antes, porque o
    `VehicleRepository.authorized_vins` devolve o `AuthorizedVehicle` da porta.
  - **Regras e skills:** o teste de 403 de escrita usa "um papel sem a permissão" (a `silver`
    no `Vehicle`, que o `leitor` nem lê). Isso ficou registrado em `rules/testes.md`, no
    `/nova-entidade` e em 03 §1.10. O smoke cria também o `smoke-silver` e ganhou oito linhas de
    veículo.
  - **Revisão do `revisor-arquitetura`:** sem bloqueantes. Um achado importante, corrigido: a
    `silver` não tem `veiculo:ler`, então o 403 dela sai do piso da classe, e um
    `@permission_required` de escrita apagado passaria na suíte. O `test_contracts.py` ganhou
    `test_toda_escrita_protegida_declara_a_propria_permissao` (escrita não pública precisa de
    decorator no próprio método; conferido apagando o do PATCH de veículo). Os textos que diziam
    "só o teste de 403 pega" foram corrigidos (03 §1.10 e checklist, `rules/rotas.md`,
    `rules/testes.md`, `/nova-entidade` e `test_authz.py`).
  - **Ambiente:** o `Result.tuples()` do SQLAlchemy 2.1 está obsoleto (o `Row` já desempacota
    com tipo); com o `select` do SQLAlchemy em vez do `db.select`, o mypy vê o tipo das colunas.
- **2026-11-07 — passo 1.2b: só o `app.py` na raiz de `app/`.** Pedido do usuário, que escolheu
  o `domain/` para o vocabulário (no lugar de um pacote `kernel/`).
  - **Para onde foram:** `authz.py`, `vin.py` e `ports.py` → `domain/` (folhas);
    `request_context.py` → `instrumentation/`; `http_errors.py` → `blueprint/`.
  - **A regra das folhas:** `tests/unit/test_fronteiras.py` nasce com ela (o ORM `base`/`models`
    é a exceção nomeada) e já reprovou de propósito `import flask` e `from domain import models`.
  - **Regras:** nasce `rules/dominio.md`. Os `paths` de `modelos-e-migracoes.md`,
    `autorizacao.md` e `extensoes-e-config.md` seguiram os arquivos.
  - **Ambiente:** o `ruff check --fix` é bloqueado, então a ordem dos imports foi corrigida à mão.
  - **Revisão** (`revisor-arquitetura`, sem bloqueantes). O que mudou por causa dela:
    - o `test_fronteiras` passou de lista de proibidos para lista de permitidos. A versão
      anterior aceitava `psycopg`, `werkzeug` ou `jwt` numa folha;
    - o teste ganhou a regra do ORM e a do `__init__.py` sem import;
    - o `test_composicao` passou a proibir o `blueprint` no consumer;
    - a decisão ganhou o **Custo aceito** (o ORM reconhecido pelo nome) e o `domain/orm/` no
      **Descartado**;
    - a `01-arquitetura` e a `extensoes-e-config.md` agora dizem por que `ext` → `blueprint`
      não forma ciclo;
    - a `dominio.md` (e a `mensageria.md`, que já faltava) entrou no mapa e na lista do revisor.
  - **Fim de linha:** o `smoke.sh` estava com CRLF no disco, apesar do `eol=lf`. O conteúdo no
    git era o mesmo; voltou para LF com `sed -i 's/\r$//'`.
- **2026-11-07 — passo 1.3 do pipeline (o producer publicando).** O contrato
  `telemetry.batch.v1`, a rota `POST /telemetry/batches`, o serviço do pipeline, o publisher e a
  topologia (`flask rabbitmq setup` no job `setup`).
  - **Desvios do plano:**
    - a topologia nasce sem DLX, DLQ e policy: o primeiro uso deles é o consumer (1.4) e a
      contrapressão (Fase 5). A fila já é quorum, porque o tipo não muda depois;
    - o publisher já reabre uma vez a conexão reaproveitada que falha, adiantado da Fase 5: sem
      isso, o primeiro lote depois de um worker ocioso daria 503;
    - o contrato tem os próprios `SENDER_RELATIONS`, em vez de importar o `SenderRelation`: um
      vínculo novo no domínio não muda a v1 por tabela, e um teste compara os dois;
    - as datas do envelope são `isoformat()` com microssegundos e `+00:00`, e não o `Z` do
      exemplo do plano: é o que o marshmallow escreve e lê sem formato próprio;
    - o formato do lote é o básico (UUID, VIN, data com fuso, limites de contagem, padrão da
      métrica). Tipos estritos, `available` ⇔ `value`, unicidade e o 413 ficam para a Fase 4;
    - a regra de fronteira do pipeline (`service/telemetry/` só com portas, folhas e
      `service.errors`/`pagination`) nasceu já agora no `test_fronteiras.py`, e não no 1.4;
    - o `api` deixou de depender direto do `rabbitmq` no compose: depende do `setup`, que espera
      o broker;
    - o `setup` dá ao usuário da `AMQP_URL` permissão `.*` no vhost, até os usuários por
      processo da Fase 7;
    - `RABBITMQ_VHOST` no compose (padrão `drive-data`) para o smoke usar um vhost descartável.
      O `RABBITMQ_DEFAULT_VHOST` continua fixo, para não recriar o container do broker.
  - **Regras:** nascem `rules/contratos.md` e `rules/servicos-telemetria.md`; a
    `mensageria.md` ganhou o publisher, a topologia e a API de gerenciamento com POST; a
    `dominio.md` cita o `contracts/`; a `testes.md`, as fixtures de fila e o padrão do
    `override`. Valem a partir da sessão seguinte.
  - **Ambiente:**
    - a contagem de mensagens de fila quorum no `rabbitmqctl list_queues` atrasa alguns
      segundos. O smoke achou 0 logo depois do 202 confirmado; passou a esperar até 15 s;
    - editar `.sh` por heredoc do Python deixou um CR solto e comeu uma continuação de linha
      (`\` + quebra). Script com barra invertida vai em arquivo no scratchpad, não em heredoc.
  - **Revisão** (`revisor-arquitetura`, sem bloqueantes). Um achado importante, corrigido: o
    `flask rabbitmq setup` mora no `ext/rabbitmq.py`, e a receita 8 dizia que todo comando vai
    no `ext/commands.py`. Virou regra, em 03 §8, em 02-decisoes (job `setup`) e em
    `extensoes-e-config.md`: o comando de operação de uma infraestrutura fica na extensão dona
    dela, porque o perfil `[setup]` não carrega o `ext.commands`. As sugestões também entraram:
    o `Singleton` e o `providers.Configuration` em 03 §1.6 e na regra; o pika importado pelo
    container em todo processo HTTP, registrado em 01; o 403 também com o `leitor`; o
    `__init__.py` sem import conferido em `domain/contracts/` e `service/telemetry/`; a v2 da
    §11.2 cita a porta e o publisher. Ficou de fora o `Retry-After` no OpenAPI: o
    `error_responses` não declara header, e a descrição da rota já o explica.
- **2026-11-07 — passo 1.4 do pipeline (o consumer gravando no bronze).** O `flask consumer run`
  (`ext/consumer.py` e `ext/consumer_container.py`), o `messaging/consumer.py`, o
  `BronzeIngestService`, o `repository/mongo/` e o `flask mongo setup`, o serviço `consumer` no
  compose e os fechos de import no `test_fronteiras.py`.
  - **Desvios do plano:**
    - **a semântica do `nack` e do `reject` está invertida em relação à §5 do plano.** No
      RabbitMQ 4.3+, a fila quorum não conta o `nack` com requeue para o `delivery-limit`, e
      conta o `reject` com requeue e a queda da conexão com a mensagem pendente (conferido na
      documentação e contra o broker, em `tests/rabbitmq/test_consumo.py`). Por isso a falha
      transitória usa `nack` e o bug usa `reject`. O "fecha o canal quando o Mongo cai" da Fase 5
      está errado nessa versão: mandaria mensagens boas para a DLQ numa queda longa do Mongo;
    - DLX, DLQ e policy nasceram aqui (o 1.3 os deixou para o primeiro uso). O
      `max-length-bytes` continua na Fase 5;
    - a pausa com backoff (0,5 → 30 s, com `connection.sleep`) e o handler de SIGTERM vieram da
      Fase 5: sem eles, o critério "consumer parado → a fila acumula; de volta, drena" não se
      sustenta (o PID 1 ignora o SIGTERM sem handler). A reconexão no processo e o servidor de
      saúde em :8081 continuam na Fase 5; até lá, a queda do broker encerra o processo com 1 e
      o `restart: unless-stopped` o sobe de novo;
    - o `available_metrics` já é calculado (o plano o deixava na Fase 4), porque o documento
      nasce com o layout final; sem TTL (Fase 7), sem `telemetry_rejections` e sem a distinção
      duplicado × colisão (Fase 4): duplicado recebe ack e vai para o log;
    - `AMQP_PREFETCH` e `AMQP_DELIVERY_LIMIT` no `settings.toml`;
    - o fecho do producer não segue o `ext.container`, raiz de composição dos dois processos
      HTTP: no 1.5 ele passa a montar o leitor do bronze do `bronze-api`, e seguir o container
      reprovaria por uma ligação que o perfil `[api]` não carrega. O fecho parte da rota, do
      serviço e do publisher; o do consumer, de todas as extensões do perfil `[consumer]`.
  - **Regras:** nasce `rules/repositorios-mongo.md`; a `mensageria.md` ganhou o consumer e a
    contagem do `delivery-limit`; a `servicos-telemetria.md`, o lado `bronze_*`; a `testes.md`,
    as fixtures novas e o contexto de aplicação; a `infra.md`, o serviço `consumer`; a
    `extensoes-e-config.md`, o `ext.mongo` na ressalva e o container do consumer. O `CLAUDE.md`
    ganhou o invariante 8 e o serviço; o hook, o `consumer`. Valem a partir da sessão seguinte.
  - **Lições:**
    - o `Database.command()` do pymongo não herda o `codec_options` do banco: sem passá-lo, o
      `hello.localTime` volta sem fuso, ao contrário do que o cliente `tz_aware` lê;
    - o `with_appcontext` da CLI reaproveita o contexto de aplicação que encontra aberto. Uma
      fixture (`mongo_db`) que deixava o contexto da aplicação mínima do Mongo aberto fez o
      `flask rabbitmq setup` do e2e rodar contra ela e reprovar com "AMQP_MANAGEMENT_URL não
      definida". A fixture deixou de abrir contexto; o conftest de `tests/mongo/` o abre;
    - a DLQ não tem policy: fica com o `delivery-limit` padrão (20) e sem DLX, e a mensagem
      devolvida 20 vezes com `reject` some. Registrado em 02-decisoes; os comandos da DLQ (Fase
      5) precisam confirmar ou republicar, nunca devolver;
    - o critério com os processos reais foi conferido num projeto do compose à parte
      (`-p ddverif`, um override sem portas no host e `down -v` no fim), para não tocar no
      ambiente de desenvolvimento de pé. O `docker compose stop consumer` levou de 2 a 7 s, com
      "Consumer parado." no log. Fica como molde para o `/smoke-pipeline` do passo 1.5;
    - dentro do `pymongo.timeout`, a espera por um servidor é o prazo da operação, e não o
      `serverSelectionTimeoutMS` do cliente. E, depois de uma recusa de credencial, o PyMongo
      marca o servidor como desconhecido: a operação seguinte sai como
      `ServerSelectionTimeoutError`, não como `OperationFailure`.
  - **Revisão** (`revisor-arquitetura`; a primeira rodada parou no limite de uso e foi retomada).
    Um bloqueante, corrigido: a 03 §7 ainda dizia "as duas extensões" importadas por outras, e
    com o `ext.mongo` são três. Também corrigidos:
    - o prompt do revisor aceitava só `ext.config` e `ext.database` como exceção;
    - o motivo de o fecho do producer não seguir o `ext.container` estava errado no registro;
    - o fecho do consumer partia só de `ext.consumer` e `ext.consumer_container`, e um import
      escondido no `ext.rabbitmq` ou no `ext.observability` passaria. Agora parte de todas as
      extensões do perfil `[consumer]`, lidas do `settings.toml`;
    - o "só compartilham folhas (contrato, portas, VIN)" ficou preciso: o consumer não usa o
      VIN, e os dois usam o `messaging/connection.py`;
    - a docstring do `repository/__init__.py` e a trava `idle_timeout` no teste da parada.

    A sugestão de robustez virou código. Antes, credencial ou permissão recusada caía no caminho
    do bug, e uma senha errada mandaria a fila inteira para a DLQ. Agora é falha transitória,
    como o Mongo fora. Cada operação também ganhou um prazo (`BRONZE_WRITE_TIMEOUT_SECONDS`):
    sem ele, um servidor que aceita a conexão e não responde prenderia o consumer até a queda
    do heartbeat.
- **2026-11-07 — passo 1.5 do pipeline (a API da silver).** O `GET /bronze/records` no
  `bronze-api` (a rota, o `bronze_schema.py` com o codec do cursor, o `BronzeReadService`, o
  `MongoBronzeReader` e a `CursorPage`), o `tests/e2e/test_pipeline.py` e a skill
  `/smoke-pipeline`.
  - **Desvios do plano:**
    - a sentinela `ANY_OWNER` e o tipo `OwnerScope` saíram do `service/base_service.py` para a
      folha `domain/authz.py`. O pipeline não pode importar o `BaseService`, e assim o serviço
      da silver recebe o escopo explícito e sem padrão, como o CRUD (02-decisoes);
    - o prazo de cada operação e a classificação da falha do Mongo saíram do `bronze_writer.py`
      para o `repository/mongo/server.py`, comum ao writer e ao leitor;
    - o 503 da leitura não leva `Retry-After`: a silver repete no próprio intervalo de polling,
      com o mesmo cursor;
    - `BRONZE_READ_TIMEOUT_SECONDS` (5 s) no `settings.toml`, ao lado do prazo de gravação;
    - os filtros e o cursor amarrado a eles continuam na Fase 4;
    - a janela de estabilização é de 2 s no smoke (`FLASK_BRONZE_SETTLE_SECONDS` no override).
      O e2e no processo a zera sobrepondo o provider do serviço (a fixture `sem_janela`).
  - **Regras:** a `repositorios-mongo.md` ganhou o leitor, o `server.py`, o `readConcern` e o
    teste do plano; a `servicos-telemetria.md`, o lado da silver e o escopo de dono; a
    `schemas.md`, o `CursorField` e o contrato da `CursorPage`; a `testes.md`, os dublês e os
    overrides novos; a `servicos.md`, o lugar novo do `OwnerScope`. O `CLAUDE.md` ganhou o
    `/smoke-pipeline`. Valem a partir da sessão seguinte.
  - **Lições:**
    - o mypy reprova o override que renomeia um argumento (`_attr` no lugar de `attr`), e o
      ruff reprova (`ARG002`) o argumento não usado com o nome original. O `_serialize` do
      `CursorField` repassa tudo ao `super()._serialize`, e os dois ficam satisfeitos;
    - `limit=0` no `find` do PyMongo quer dizer "sem limite". O leitor o recusa com
      `ValueError`, para um bug não virar uma varredura da coleção;
    - para provar que o reenvio não repetiu nada sem esperar um prazo arbitrário, o smoke manda,
      depois do reenvio, um lote sentinela de 1 registro. Com um consumer só, a fila entrega na
      ordem: quando o sentinela aparece na silver, o reenvio já foi processado, e a página traz
      só o sentinela;
    - o `/smoke-pipeline` leva ~4 min (137 s só no `up --wait`), contra ~30 s do `/smoke-api`.
      Não serve para iterar: é o portão do passo.
  - **Revisão** (`revisor-arquitetura`, sem bloqueantes). Um achado importante, corrigido: a 01
    e o `test_composicao.py` diziam que a extensão fora do perfil não é importada, e o `api`
    passou a importar o `ext.mongo` pelo leitor do bronze, que o container declara. Agora dizem
    "não é inicializada", e um teste novo monta o `api` sem `MONGO_URI` e confere que ele fica
    sem o cliente, sem o `flask mongo` e sem a sonda. Também corrigidos:
    - o que o writer e o leitor compartilham, na 01;
    - a docstring do `bronze_document.py`, que apontava o teste errado;
    - as docstrings de `domain/authz.py` e `domain/ports.py` e as regras `autorizacao.md` e
      `schemas.md`, sobre o escopo de dono e os limites da página do bronze;
    - o `test_contracts.py` ficou mais estrito: o campo novo do `BronzeEntry` reprova até entrar
      no schema ou em `OMITIDOS_DA_SILVER`;
    - na 02 e no `bronze_schema.py`: o v1 do cursor continuará aceito como a leitura sem filtro
      quando os filtros chegarem (Fase 4), para a silver não reler o bronze.

    Ficou de fora reaproveitar o `BRONZE_START` como a época do codec do cursor: os valores
    coincidem, mas um é a posição inicial da leitura e o outro, a origem dos milissegundos.
- **2026-11-08 — Fase 2 do pipeline (os motoristas).** O `VehicleDriver` (modelo e migração
  0004, com o índice único parcial do vínculo ativo), o repositório, o `VehicleDriverService`, os
  schemas, a rota `vehicle_driver.py` (a visão do gestor em `/vehicle/<id>/drivers` e a do
  motorista em `/vehicle/driving`), o `authorized_vins` com os motoristas, o veículo excluído
  desligando os vínculos e a receita 13 (sub-recurso) da 03.
  - **Desvios do plano:**
    - as relações muitos-para-um da saída vão com `joinedload`, e não com `selectinload`: medido
      com o `/medir-consultas`, a lista de motoristas custa 4 consultas, contra 7 com o
      `selectinload` e 24 sem eager load (02-decisoes, "Relação muitos-para-um na saída vai com
      `joinedload`"). As contagens ficam no `tests/test_read_cost.py`, que é novo;
    - o `DrivingSchema` leva o `owner_name`: o motorista precisa saber de quem é o carro;
    - o login do motorista passa pelo `_clean_text` antes da busca;
    - o 404 do login desconhecido conta a quem tem `veiculo:escrever` que aquele login não
      existe. É o custo aceito (02-decisoes, "Motorista é um vínculo com o veículo, não um
      papel"): o cadastro público da Fase 3 revelará o mesmo pelo 409;
    - no `/smoke-api`, o lote de telemetria passa a ser enviado pelo `gerente` como motorista do
      veículo do `usuario`, e o motorista removido recebe 202 com `accepted == 0`. No
      `/smoke-pipeline`, um motorista criado pela CLI manda o lote sentinela e a silver confere
      o `user_id` e o `sender_relation`;
    - o comentário dos perfis no `settings.toml` dizia que a extensão fora do perfil "não é
      importada"; corrigido como a revisão do 1.5 pediu para a 01.
  - **Regras:** a `repositorios.md` ganhou a escolha entre `joinedload` e `selectinload`; a
    `servicos.md` e a `rotas.md`, o sub-recurso (o lado do gestor pelo pai, a outra ponta pelo
    id do token); a `testes.md`, a cobertura do sub-recurso e o `test_read_cost.py`. A skill
    `/medir-consultas` ganhou a medição dos motoristas e a mesma escolha; o `/verificar`, o
    `test_read_cost.py` no diagnóstico; o `CLAUDE.md`, a receita nova na lista. Valem a partir
    da sessão seguinte.
  - **Lições:**
    - o `selectinload` de uma relação muitos-para-um emite a consulta mesmo quando o objeto já
      está na sessão (o veículo de todos os vínculos da página): ele custa uma consulta por
      relação, sempre. O `joinedload` vem na própria consulta da página e, como a relação é
      muitos-para-um, não multiplica linhas nem atrapalha o `LIMIT`;
    - a resposta do cliente de teste do Flask não tem `.ok`: use `status_code`.
  - **Contagens:** `/verificar tudo` com 391 testes; `/smoke-api` com 50 verificações;
    `/smoke-pipeline` com 21.
  - **Revisão** (`revisor-arquitetura`, sem bloqueantes). Quatro achados importantes,
    corrigidos:
    - o molde `Item` (`item_repository.py`), a docstring do `base_repository.py` e a nota da
      03 §1.3 ainda davam o `selectinload(X.owner)` como exemplo; agora dão o `joinedload`;
    - a 01, a `servicos.md` e o passo 3 da receita 13 diziam que **toda** operação do
      sub-recurso começa pelo pai, e a visão do motorista não começa. Agora dizem "toda operação
      do gestor", com a outra ponta filtrando pelo id do token; a `rotas.md` passou a dizer que
      a criação do filho usa o escopo de todos do pai, e não o `current_user.id`;
    - nenhum teste tinha um vínculo de outro par: tirar o filtro do motorista do
      `list_driving`, filtrar o `leave` só pelo veículo ou o `remove_driver` só pelo motorista
      passava na suíte. O `test_nenhuma_operacao_alcanca_o_vinculo_de_outro_par` reprova as
      três mutações (conferidas uma a uma), e a `testes.md` e a receita 13 pedem esse caso;
    - faltava esta entrada do registro.

    As três sugestões entraram: a seta "serviço → serviço do pai", com a assimetria que evita o
    ciclo, nas regras de dependência da 01; o **Descartado** e o **Custo aceito** da decisão do
    `joinedload` na 02; o `test_read_cost.py` no `/verificar` e na descrição do
    `/medir-consultas`.
- 2026-11-08 — Pipeline de telemetria, Fase 3 (autenticação completa e saída do `Item`): cadastro
  público, par de tokens com rotação, reuso que revoga a família, logout, JWT de 15 min com `iss`,
  `flask set-password` e `prune-tokens`, migrações 0005 e 0006. O molde passou a ser o `Vehicle`.
  - **Desvios do plano:**
    - sem `token_in_blocklist_loader` nem `revoked_token_loader`: o token reusado precisa chegar
      ao serviço, que decide sob trava. O `refresh_token_required` só confere assinatura,
      validade e usuário;
    - revogar é exclusão lógica (`deleted`), sem `revoked_at`, e o `family_id` é o `jti` do
      primeiro token;
    - a senha do login tem limite de 128 caracteres no schema, e as mensagens de token são
      neutras ("Token inválido.");
    - a 0006 transforma as contas `leitor` e `gerente` em `usuario`;
    - como nenhum papel real só lê, o 403 de quem só lê é provado restringindo o mapa com
      `monkeypatch.setitem` em `ROLE_PERMISSIONS`.
  - **Regras alteradas:** todas as que citavam o `Item` apontam para o `Vehicle`. A
    `autorizacao.md` ganhou o `refresh_token_required`; a `servicos.md`, a exceção do
    `TokenService`; a `rotas.md`, o 401 da rota pública; a `testes.md`, o 403 com a `silver` e o
    teste do papel que só lê. O `/medir-consultas` mede sobre o `Vehicle` (mesmos números). Valem
    a partir da sessão seguinte. O `/remover-exemplo` do roadmap foi feito à mão, sem skill.
  - **Lições:**
    - teste de concorrência com barreira e threads não garante a sobreposição: sem o `FOR UPDATE`
      ele continuava passando. O que reprova a mutação é a transação principal segurar a linha
      enquanto a outra tenta (`tests/test_refresh.py`);
    - com `@inject` no `__init__` da classe base, a filha que chama `super().__init__(arg)` leva
      `multiple values for argument`: a filha injeta só o que é dela e chama `super().__init__()`;
    - o `Result` do SQLAlchemy não tipa `rowcount`: `cast("CursorResult[Any]", result)`;
    - o `/verificar testes` esconde a saída quando passa: para ver os `print` de medição, use
      `verificar.sh medir`.
  - **Contagens:** `/verificar tudo` com 418 testes; `/smoke-api` com 51 verificações;
    `/smoke-pipeline` verde.
  - **Revisão** (`revisor-arquitetura`, sem bloqueantes). Quatro achados importantes, corrigidos:
    - o `TokenService` sem `owner_scope` não estava registrado como exceção: entrou na 02 e na
      `servicos.md`;
    - o `@refresh_token_required` marcava o método como "declarou", e uma rota nova com ele
      passaria no contrato sem permissão: o
      `test_so_renovar_e_encerrar_a_sessao_aceitam_o_token_de_renovacao` fixa as duas rotas;
    - a migração e a 02 diziam que o `leitor` não ganhava acesso, e ele ganha escrita. O texto foi
      corrigido, e o checklist de produção manda conferir essas contas antes da 0006;
    - o README não listava o `/auth/register` entre as rotas públicas.

    Das cinco sugestões, entraram todas: o 401 na `rotas.md`, o `op.f()` na 0006, a asserção vaga do
    `set-password`, a preferência pelo prompt da senha no README e a pendência antiga do
    `leitor`, que já estava resolvida pelo contrato e saiu de "Pendências".
