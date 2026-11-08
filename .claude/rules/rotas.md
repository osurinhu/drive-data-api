---
paths:
  - "app/blueprint/**/*.py"
---

# Camada de rota (`app/blueprint/restapi/v1/`)

Traduz HTTP em chamada de serviço. **Não pode:** montar consulta, conter regra de negócio,
usar `try/except` para erro de domínio, decidir permissão com `if` nem serializar à mão.
Molde: `app/blueprint/restapi/v1/item.py`. Receitas: 03-como-estender §1.8, §2 e §10.1.

- Um módulo por recurso, expondo `blp = Blueprint(...)`, ativado por uma linha em
  `API_V1_ROUTES` do `[default]` **e** outra no perfil do processo que serve a rota (`[api]`
  para as de negócio; 03-como-estender §1.9). O container só faz o *wiring* de `Provide[...]`
  neste pacote.
- Classe base do recurso com o **piso**: `decorators = [permission_required(Permission.X_LER)]`
  e `@inject` no `__init__` com `Provide[Container.x_service]`. Método sem decorator herda o piso:
  fica fechado para quem não lê — mas o piso é de leitura.
- **Cada** método de escrita leva o seu `@permission_required(Permission.X_ESCREVER)`, **acima**
  dos decorators do smorest (quem não pode leva 403 antes de descobrir o corpo num 422). Esquecido,
  a escrita abriria para quem só lê: o `test_contracts.py` reprova escrita não pública sem
  decorator no próprio método, e o teste de 403 com `Role.LEITOR` prova que a permissão é a certa.
- Qualquer autenticado: `decorators = [authenticated]` (de `ext.auth`). Rota pública:
  `@blp.doc(security=PUBLIC)` (de `ext.api`) **e** a tupla em `ROTAS_PUBLICAS`
  (`tests/test_contracts.py`); faltando um dos dois, o CI reprova.
- Escopo de dono: leitura `current_owner_scope(Permission.X_LER_DE_TODOS)`; escrita e exclusão
  `current_owner_scope(Permission.X_ESCREVER_DE_TODOS)`; criação `current_user.id` (criar exige
  dono concreto — `ANY_OWNER` levantaria `TypeError`).
- `@error_responses(blp, 403, 404, 409)` (de `blueprint.http_errors`) lista o que **esta** rota produz.
  Nunca 401 (global no spec). 422 só em rota sem `@blp.arguments` que possa levantar
  `BusinessRuleError` (como o `arquivar` da §2); onde há `@blp.arguments`, o smorest já o documenta.
- Sub-recurso: módulo próprio com o `url_prefix` do pai e outro nome de blueprint, com as
  permissões e os escopos do pai em toda operação do dono do pai, inclusive a criação do filho
  (`current_owner_scope(Permission.X_ESCREVER_DE_TODOS)`: o filho nasce num pai que já tem
  dono). A visão da outra ponta do vínculo (`/vehicle/driving`) passa `current_user.id`
  (`vehicle_driver.py`; 03-como-estender §13).
- O payload de `@blp.arguments` vem antes do parâmetro de URL: `def patch(self, payload, x_id)`.
- A docstring do método é pública: o 1º parágrafo vira o `summary` do OpenAPI e o resto, a
  `description`. Escreva-a em pt-BR para o cliente da API; nota para desenvolvedor vai em `#`.
- `health.py` não é molde: não tem serviço, executa as sondas que as extensões registraram
  (`instrumentation.probes.registered_probes`) e decide o 503 pelo `HEALTH_CRITICAL` do perfil.
  Não importa extensão de serviço externo nem trata exceção. Ver 02-decisoes, "`/health/`
  verifica cada dependência do processo; 503 só para as críticas".
- `B008` é ignorado nesta pasta por causa do `Provide[...]` como default; o mypy não cobre a camada.
