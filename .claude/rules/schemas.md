---
paths:
  - "app/schemas/**/*.py"
---

# Schemas (`app/schemas/`)

Único contrato de entrada e saída da API. **Não pode:** acessar banco nem importar outras
camadas; pode importar outros módulos de `schemas` e as folhas do domínio (`domain.authz`, como
faz `auth_schema.py`, e `domain.vin`), nunca o ORM (`domain.models`). Molde: `app/schemas/item_schema.py`. Receitas: 03-como-estender §1.5 e §6.

- Cinco schemas por entidade: `XSchema` (saída), `XCreateSchema` (POST), `XUpdateSchema` (PATCH,
  tudo opcional, **com** o `@validates_schema` "Informe ao menos um campo" do `ItemUpdateSchema`),
  `XQuerySchema` (herda `PaginationQuerySchema`) e `XPageSchema` (herda `PageSchema` e declara
  `items`).
- Limites como constantes do módulo (`NOME = validate.Length(min=1, max=100)`), usadas na
  criação e na atualização. Acompanham o `String(n)` da coluna: divergir dá `DataError` (500) em
  vez de 422 — e a coluna não pode ser importada daqui.
- O schema de saída é a única fonte do que sai: campo fora dele não aparece. Não use `dump_only`
  nele (nunca é usado em `@blp.arguments`). Nunca exponha `password`.
- Limites de paginação moram em `pagination_schema.py`, não no `settings.toml`.
- `PageSchema` e `service.pagination.Page` descrevem o mesmo envelope, e `ApiErrorSchema` documenta
  o de erro: `test_contracts.py` reprova se divergirem.
- O mypy cobre esta camada.
