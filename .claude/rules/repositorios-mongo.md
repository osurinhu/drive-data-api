---
paths:
  - "app/repository/mongo/**/*.py"
---

# Repositórios Mongo (`app/repository/mongo/`)

O bronze: a coleção `telemetry_records`, gravada pelo consumer e lida pela silver, e a
`telemetry_rejections`, com o que o consumer recusou. Os adaptadores implementam portas de
`domain/ports.py` (`BronzeWriter`, `BronzeReader`, `RejectionLog`) e o
container do processo os escolhe. **Não pode:** conter regra de negócio nem decidir permissão.
As decisões estão em 02-decisoes, "A gravação no bronze é idempotente pelo `_id`", "A silver lê
o bronze por cursor" e "Cliente do Mongo sem conexão até o primeiro uso". Receita:
03-como-estender §12.

- Imports: pymongo, as folhas do domínio (as portas) e `ext.mongo` (o cliente, `mongo.db`). Nada
  de `sqlalchemy`, `ext.database`, `repository.sql`, `domain.models` nem `service`:
  `tests/unit/test_fronteiras.py` reprova (`FORA_DA_TECNOLOGIA`).
- O layout do documento mora só em `bronze_document.py` (`to_document`, `from_document`,
  `to_rejection_document`, `sender_of`, o nome das coleções). O serviço monta e recebe um
  `BronzeEntry` e não conhece o BSON; o adaptador não monta nem lê documento à mão.
- `indexes.py`, `users.py`, `bronze_document.py` e `server.py` não importam `ext`: o `ext.mongo`
  importa o `indexes` e o `users` para o `flask mongo setup`, e um import de volta faria o
  ciclo. Quem precisa do cliente (`bronze_writer.py`, `bronze_reader.py`, `rejection_log.py`) o pede ao `ext.mongo` na
  hora do uso, nunca no import, e passa o banco ao `server.py`.
- O `_id` é o `id` do registro, em texto: é o índice único dele que torna a gravação
  idempotente. Ids vão como texto e datas como `datetime` com fuso (o BSON guarda milissegundos).
  O `raw` é gravado como chegou, sem normalizar nada.
- Gravação com `w="majority", j=True` (`DURABLE`) por `server.insert_skipping_duplicates`
  (`insert_many(ordered=False)`). Chave duplicada (11000) é "já estava lá", e não erro. Recusa
  tem `_id` `<message_id>:<record_id>`: a reentrega não a duplica. Leitura com
  `readConcern: majority` (`MAJORITY`), o par do `DURABLE`.
- Toda operação roda dentro de `server.operation(timeout)`, com o prazo que o container passa
  (`BRONZE_WRITE_TIMEOUT_SECONDS` no consumer, `BRONZE_READ_TIMEOUT_SECONDS` no bronze-api). Ele
  é o `pymongo.timeout` mais a classificação da falha, igual para o writer e o leitor.
- Falha da infraestrutura vira `BronzeUnavailable` (a porta: transitório; no consumer, a
  mensagem volta para a fila sem contar; no bronze-api, 503): `ConnectionFailure`, erro de prazo
  (`error.timeout`), a recusa da configuração (`REFUSED_BY_CONFIGURATION`: credencial 18,
  permissão 13) e, na gravação, `writeConcernErrors`. A regra é "atingiria qualquer operação".
  Qualquer outro erro do Mongo sobe como está: o consumer o trata como bug, e o `reject` conta
  para o `delivery-limit`. Na dúvida, pergunte se repetir a mesma operação mais tarde pode dar
  certo.
- Data do servidor: `server.server_time(database, timeout=...)`, com
  `codec_options=database.codec_options`. O `command` não herda as opções do banco, e a data
  voltaria sem fuso.
- Leitura da silver: o filtro vem de `page_query` (o intervalo no campo de gravação, o
  desempate num `$or`, e os `BronzeFilters`), com `sort=ORDER` e `limit`. `limit=0` é "sem
  limite" no PyMongo: o leitor o recusa. Consulta nova ou índice novo de leitura ganham o teste do plano
  (`explain`, sem `SORT`), no molde do `tests/mongo/test_bronze_reader.py`.
- Índice novo em `indexes.py`, com nome explícito (`ix_<campos>`), aplicado pelo
  `flask mongo setup`. Mudar a definição de um índice existente reprova: outro nome, e o antigo
  sai numa mudança à parte. A exceção é o prazo do TTL, que é configuração: o `_apply_ttl` o
  ajusta com `collMod`. Prazo 0 apaga o índice, porque `expireAfterSeconds: 0` expira tudo na
  hora.
- Usuários em `users.py`: um por processo, com o menor papel embutido que serve (`readWrite` no
  consumer, `read` no bronze-api), no próprio banco do bronze. Escrita nova no `bronze_reader.py`
  passa nos testes da pasta, que usam root, e reprova em produção: acrescente o caso em
  `tests/mongo/test_usuarios_mongo.py`.
- Testes contra o Mongo de verdade em `tests/mongo/` (banco `<MONGO_DATABASE>_test`, limpo a
  cada teste; o conftest da pasta abre o contexto de aplicação que o `mongo.db` exige e dá o
  `mongo_avulso`, um Mongo em outro endereço, para a falha da infraestrutura). A ida e a volta
  do documento se testam sem Mongo, em `tests/unit/test_leitura_bronze.py`.
- O mypy cobre esta camada.
