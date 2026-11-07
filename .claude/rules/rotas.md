---
paths:
  - "app/blueprint/**/*.py"
---

# Camada de rota (`app/blueprint/restapi/v1/`)

Traduz HTTP em chamada de serviço. **Não pode:** montar consulta, conter regra de negócio,
usar `try/except` para erro de domínio, decidir permissão com `if` nem serializar à mão.
Molde: `app/blueprint/restapi/v1/item.py`. Receitas: 03-como-estender §1.8, §2 e §10.1.

- Um módulo por recurso, expondo `blp = Blueprint(...)`, ativado por uma linha em
  `API_V1_ROUTES` (`settings.toml`). O container só faz o *wiring* de `Provide[...]` neste pacote.
- Classe base do recurso com o **piso**: `decorators = [permission_required(Permission.X_LER)]`
  e `@inject` no `__init__` com `Provide[Container.x_service]`. Método sem decorator herda o piso:
  fica fechado para quem não lê — mas o piso é de leitura.
- **Cada** método de escrita leva o seu `@permission_required(Permission.X_ESCREVER)`, **acima**
  dos decorators do smorest (quem não pode leva 403 antes de descobrir o corpo num 422). Esquecido,
  a escrita abre para quem só lê, e o `test_contracts.py` não percebe (aceita o piso da classe):
  só o teste de 403 com `Role.LEITOR` daquela operação pega.
- Qualquer autenticado: `decorators = [authenticated]` (de `ext.auth`). Rota pública:
  `@blp.doc(security=PUBLIC)` (de `ext.api`) **e** a tupla em `ROTAS_PUBLICAS`
  (`tests/test_contracts.py`); faltando um dos dois, o CI reprova.
- Escopo de dono: leitura `current_owner_scope(Permission.X_LER_DE_TODOS)`; escrita e exclusão
  `current_owner_scope(Permission.X_ESCREVER_DE_TODOS)`; criação `current_user.id` (criar exige
  dono concreto — `ANY_OWNER` levantaria `TypeError`).
- `@error_responses(blp, 403, 404, 409)` (de `http_errors`) lista o que **esta** rota produz.
  Nunca 401 (global no spec). 422 só em rota sem `@blp.arguments` que possa levantar
  `BusinessRuleError` (como o `arquivar` da §2); onde há `@blp.arguments`, o smorest já o documenta.
- O payload de `@blp.arguments` vem antes do parâmetro de URL: `def patch(self, payload, x_id)`.
- A docstring do método é pública: o 1º parágrafo vira o `summary` do OpenAPI e o resto, a
  `description`. Escreva-a em pt-BR para o cliente da API; nota para desenvolvedor vai em `#`.
- Exceção prevista, não molde: `health.py` executa `SELECT 1` com o `db` de `ext.database` e trata
  a falha com `try/except` (503). É a sonda de disponibilidade (02-decisoes, "`/health/` verifica
  o banco, não só o processo").
- `B008` é ignorado nesta pasta por causa do `Provide[...]` como default; o mypy não cobre a camada.
