---
paths:
  - "app/domain/contracts/**/*.py"
  - "tests/data/**"
---

# Contratos da fila (`app/domain/contracts/`)

O que atravessa a fila, um módulo por mensagem e por versão. São folhas do domínio
(`rules/dominio.md`): só stdlib e marshmallow. A decisão está em 02-decisoes, "O contrato da fila
é uma folha versionada, com valores próprios"; a receita, em 03-como-estender §11.

- O módulo tem tudo da mensagem: as dataclasses (`frozen=True, slots=True`), o `encode` e o
  `decode`, a `ContractError` e o endereço (`EXCHANGE`, `ROUTING_KEY`). Quem publica não conhece
  a fila (`messaging/topology.py`), só o endereço.
- O decoder é *tolerant reader*: `unknown=EXCLUDE` em todo schema privado. Toda falha vira
  `ContractError` (o consumer a manda para a DLQ). Nunca vaza `ValidationError`,
  `UnicodeDecodeError` ou `JSONDecodeError`.
- A versão é conferida antes do resto, e estritamente: `True` não é `1`.
- Campo novo e opcional: valor padrão na dataclass, `load_default` no schema e o campo no exemplo
  canônico. Remover, renomear, tornar obrigatório ou mudar o sentido: um módulo novo (`_v2`),
  com routing key própria. O v1 não muda.
- Valores enumerados (`SENDER_RELATIONS`) são escritos no contrato, nunca importados do domínio.
  O teste que compara os dois reprova a divergência, e a resolução é decidir se é campo novo ou
  v2, não importar o enum.
- O contrato não valida regra de negócio (o formato do VIN, a posse): isso é do serviço de cada
  lado.
- Datas vão como `isoformat()` com fuso (`+00:00`) e microssegundos. O exemplo canônico
  (`tests/data/telemetry_batch_v1.json`) fixa o formato no fio. Codificar o exemplo decodificado
  devolve o mesmo JSON. Se isso mudar, o fio mudou.
- `__init__.py` sem import: o consumer importa o contrato sem carregar outro.
- Testes em `tests/unit/test_contrato_telemetria.py`, sem infraestrutura.
