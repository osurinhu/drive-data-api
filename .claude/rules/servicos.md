---
paths:
  - "app/service/**/*.py"
---

# Camada de serviço (`app/service/`)

Regra de negócio, validação semântica, ownership e transação. **Não pode:** tocar no ORM
(`db.session`, `select`) nem saber o que é HTTP (status, header, `request`, `abort`); fala com o
banco só pelo repositório. Molde: `app/service/item_service.py`; base:
`app/service/base_service.py`. Receitas: 03-como-estender §1.4, §4 e §5.

- `class XService(BaseService[X])` com `conflict_message` e `not_found_message` (as mensagens do
  409 e do 404) e `super().__init__(repository, owned=True)` — ou `owned=False` se for global.
- Todo método que lê ou escreve dado com dono recebe `owner_scope: OwnerScope` (de
  `domain.authz`, com a sentinela `ANY_OWNER`) explícito, **sem default**: um default "sem
  filtro" vira vazamento no primeiro esquecimento (02-decisoes, "Escopo de ownership por
  chamada"). Operação nova carrega o registro com `self.get_by_id(id, owner_scope)`, que já
  devolve 404 para recurso de outro dono. A exceção é a outra ponta de um sub-recurso (abaixo).
- Regras entram pelos ganchos `before_create(data)`, `before_update(model, changes)` e
  `before_delete(model)`. Texto obrigatório: `self._clean_text(valor, "nome do x")` — o schema
  barra `""`, o serviço barra `"   "` e também protege a CLI, que não passa por schema.
- Erros: só as exceções de `service/errors.py`. Tipo novo = classe lá + linha em `ERROR_STATUS`
  (e em `STATUS_DESCRIPTION`, se o status for novo); sem isso, `test_contracts.py` reprova.
- Sub-recurso (os motoristas do veículo): `owned=False`, o serviço do pai no construtor, e toda
  operação do dono do pai começa por `self.<pai>.get_by_id(pai_id, owner_scope)`. A da outra
  ponta do vínculo (o motorista) recebe o id dele (`driver_id: int`, sem default nem versão "de
  todos") e filtra por ele. O pai recebe só o repositório do filho e o desliga no
  `before_delete` (03-como-estender §13; molde: `vehicle_driver_service.py`).
- Nada de `if` de papel: a permissão é declarada na rota. Se sentir falta, falta uma permissão.
- Transação: `commit=True` por padrão; para compor várias escritas, `commit=False` e um commit
  só no fim (como `create_many`). `_guard_integrity` traduz `IntegrityError` em `ConflictError`.
- `update` aplica exatamente o que veio, valores falsy incluídos; quem torna o PATCH parcial é o schema.
- O mypy cobre esta camada: mantenha as anotações. `ARG002` é ignorado aqui por causa dos ganchos.
