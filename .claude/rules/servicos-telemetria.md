---
paths:
  - "app/service/telemetry/**/*.py"
---

# Pipeline de telemetria (`app/service/telemetry/`)

Vale além de `rules/servicos.md` (exceções de `service/errors.py`, nada de HTTP). A decisão está
em 02-decisoes, "O pipeline não herda o `BaseService`: depende só de portas".

- Sem `BaseService` e sem repositório: o serviço recebe as portas de `domain/ports.py`
  (`VehicleAuthorization`, `TelemetryPublisher`, …) e os parâmetros pelo construtor. Quem escolhe
  o adaptador é o container (`ext/container.py`), nunca o serviço.
- Só importa a stdlib, as folhas do domínio (portas, contratos, VIN) e `service.errors` /
  `service.pagination`. Nada de `flask`, `pika`, `pymongo`, `sqlalchemy`, `ext`, `repository`,
  `messaging` ou `domain.models`. `tests/unit/test_fronteiras.py` confere com uma lista de
  permitidos (`PERMITIDOS_NO_PIPELINE`): ampliar essa lista é decisão de arquitetura.
- O nome diz o lado: `ingest_*` é o producer; `bronze_*` virá com o consumer e a silver. Um lado
  não importa o outro, e o `__init__.py` fica sem import.
- Regra pura e sem estado vai em `*_rules.py`; o serviço orquestra (uma consulta por lote,
  publicação, tradução da falha da porta em exceção de serviço).
- A falha de uma porta (`PublishFailed`) vira exceção de serviço (`ServiceUnavailableError`, com
  `retry_after`). O serviço diz quando repetir; o header é da camada HTTP.
- O remetente é sempre um argumento explícito (o usuário do token). Nada no payload autoriza:
  o `user` do app é só o indicador `payload_user_matches_sender`.
- Testes com dublês em memória em `tests/unit/test_ingestao.py` (`PosseEmMemoria`, o
  `FilaEmMemoria` do `conftest.py`); o caminho HTTP, em `tests/test_telemetry.py`.
