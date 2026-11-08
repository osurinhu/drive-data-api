---
paths:
  - "app/repository/mongo/**/*.py"
---

# Repositórios Mongo (`app/repository/mongo/`)

O bronze: a coleção `telemetry_records`, gravada pelo consumer. Os adaptadores implementam
portas de `domain/ports.py` (`BronzeWriter`) e o container do processo os escolhe. **Não pode:**
conter regra de negócio nem decidir permissão. As decisões estão em 02-decisoes, "A gravação no
bronze é idempotente pelo `_id`" e "Cliente do Mongo sem conexão até o primeiro uso". Receita:
03-como-estender §12.

- Imports: pymongo, as folhas do domínio (as portas) e `ext.mongo` (o cliente, `mongo.db`). Nada
  de `sqlalchemy`, `ext.database`, `repository.sql`, `domain.models` nem `service`:
  `tests/unit/test_fronteiras.py` reprova (`FORA_DA_TECNOLOGIA`).
- O layout do documento mora só em `bronze_document.py` (`to_document`, o nome da coleção). O
  serviço monta um `BronzeEntry` e não conhece o BSON; o adaptador não monta documento à mão.
- `indexes.py` e `bronze_document.py` não importam `ext`: o `ext.mongo` importa o `indexes`
  para o `flask mongo setup`, e um import de volta faria o ciclo. Quem precisa do cliente
  (`bronze_writer.py`) o pede ao `ext.mongo` na hora do uso, nunca no import.
- O `_id` é o `id` do registro, em texto: é o índice único dele que torna a gravação
  idempotente. Ids vão como texto e datas como `datetime` com fuso (o BSON guarda milissegundos).
  O `raw` é gravado como chegou, sem normalizar nada.
- Gravação com `w="majority", j=True` (`DURABLE`) e `insert_many(ordered=False)`. Chave
  duplicada (11000) é "já estava lá", e não erro. Toda operação roda dentro de
  `pymongo.timeout(...)`, com o prazo que o container passa (`BRONZE_WRITE_TIMEOUT_SECONDS`).
- Falha da infraestrutura vira `BronzeUnavailable` (a porta: transitório, a mensagem volta para
  a fila sem contar): `ConnectionFailure`, erro de prazo (`error.timeout`), `writeConcernErrors`
  e a recusa da configuração (`REFUSED_BY_CONFIGURATION`: credencial 18, permissão 13). A regra
  é "atingiria qualquer lote". Qualquer outro erro do Mongo sobe como está: o consumer o trata
  como bug, e o `reject` conta para o `delivery-limit`. Na dúvida, pergunte se repetir o mesmo
  lote mais tarde pode dar certo.
- Data do servidor: `database.command(..., codec_options=database.codec_options)`. O `command`
  não herda as opções do banco, e a data voltaria sem fuso.
- Índice novo em `indexes.py`, com nome explícito (`ix_<campos>`), aplicado pelo
  `flask mongo setup`. Mudar a definição de um índice existente reprova: outro nome, e o antigo
  sai numa mudança à parte.
- Testes contra o Mongo de verdade em `tests/mongo/` (banco `<MONGO_DATABASE>_test`, limpo a
  cada teste; o conftest da pasta abre o contexto de aplicação que o `mongo.db` exige). A regra
  pura do documento se testa sem Mongo, em `tests/unit/test_gravacao_bronze.py`.
- O mypy cobre esta camada.
