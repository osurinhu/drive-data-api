---
paths:
  - "app/service/telemetry/**/*.py"
---

# Pipeline de telemetria (`app/service/telemetry/`)

Vale além de `rules/servicos.md` (exceções de `service/errors.py`, nada de HTTP). A decisão está
em 02-decisoes, "O pipeline não herda o `BaseService`: depende só de portas".

- Sem `BaseService` e sem repositório: o serviço recebe as portas de `domain/ports.py`
  (`VehicleAuthorization`, `TelemetryPublisher`, `BronzeWriter`, …) e os parâmetros pelo
  construtor. Quem escolhe o adaptador é o container do processo (`ext/container.py` para o
  producer, `ext/consumer_container.py` para o consumer), nunca o serviço.
- Só importa a stdlib, as folhas do domínio (portas, contratos, VIN) e `service.errors` /
  `service.pagination`. Nada de `flask`, `pika`, `pymongo`, `sqlalchemy`, `ext`, `repository`,
  `messaging` ou `domain.models`. `tests/unit/test_fronteiras.py` confere com uma lista de
  permitidos (`PERMITIDOS_NO_PIPELINE`): ampliar essa lista é decisão de arquitetura.
- O nome diz o lado: `ingest_*` é o producer; `bronze_*` é o consumer (e, no passo 1.5, a
  silver). Um lado não importa o outro, e o `__init__.py` fica sem import. O fecho de imports
  de cada lado é conferido em `tests/unit/test_fronteiras.py`.
- Regra pura e sem estado vai em `*_rules.py`; o serviço orquestra (uma consulta por lote,
  publicação, tradução da falha da porta em exceção de serviço).
- A falha de uma porta (`PublishFailed`) vira exceção de serviço (`ServiceUnavailableError`, com
  `retry_after`). O serviço diz quando repetir; o header é da camada HTTP.
- No consumer é o contrário: o `BronzeIngestService` deixa o `BronzeUnavailable` subir, porque
  quem decide o destino da mensagem é o `messaging/consumer.py`. Duplicado não é erro: o serviço
  devolve a contagem (`BronzeIngestResult`) e registra no log.
- O `ingested_at` vem do relógio do bronze (`BronzeWriter.server_time`), lido uma vez por
  tentativa de gravação, nunca do relógio do processo.
- O remetente é sempre um argumento explícito (o usuário do token). Nada no payload autoriza:
  o `user` do app é só o indicador `payload_user_matches_sender`.
- Testes com dublês em memória em `tests/unit/test_ingestao.py` (`PosseEmMemoria`, o
  `FilaEmMemoria` do `conftest.py`) e `tests/unit/test_gravacao_bronze.py` (`BronzeEmMemoria`);
  o caminho HTTP, em `tests/test_telemetry.py`; o caminho inteiro, em `tests/e2e/`.
