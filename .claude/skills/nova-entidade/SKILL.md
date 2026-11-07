---
name: nova-entidade
description: Cria uma entidade nova ponta a ponta — modelo, migração, repositório, serviço, cinco schemas, container, permissões, rota, registro no settings.toml e testes — seguindo a receita 1 de documentacao/03-como-estender.md, com Item como molde. Use quando o pedido for criar entidade, recurso, CRUD, tabela ou API de um conceito que ainda não existe no projeto.
argument-hint: "<NomeEmPascalCase> [campos e regras]"
---

# /nova-entidade — $ARGUMENTS

## 0. Entender antes de escrever

Leia `documentacao/03-como-estender.md` §1 e §10. Os moldes reais são mais completos que os
trechos da documentação:

| Peça | Molde |
|---|---|
| modelo | `Item` em `app/domain/models.py` |
| repositório | `app/repository/sql/item_repository.py` |
| serviço | `app/service/item_service.py` |
| schemas | `app/schemas/item_schema.py` |
| rota | `app/blueprint/restapi/v1/item.py` |
| testes | `tests/test_item.py` e `tests/test_authz.py` |

Se o pedido não deixar claro, faça **uma** pergunta agrupada (AskUserQuestion) sobre:
- campos: nome, tipo, tamanho, obrigatório, default;
- dono: pertence a um usuário (`owned=True`, como Item) ou é global (`owned=False`);
- unicidade: o que é único, e se é por dono (vira índice parcial);
- permissões: quem lê e quem escreve. Padrão = Item (leitor lê; usuario escreve o próprio;
  gerente lê de todos; admin escreve em todos).

Nomes, com `Product` de exemplo: classe `Product`, módulos `product_*.py`, tabela `products`,
prefixo `/product`, permissões `product:ler`, `product:escrever`, `product:ler-de-todos` e
`product:escrever-de-todos`.

Entidade global (`owned=False`): sem `owner_id` nem lado inverso em `User`. A documentação não
fixa o padrão de permissões e escopo para esse caso — proponha ao usuário (ex.: só `X_LER` e
`X_ESCREVER`, escopo `ANY_OWNER` explícito na rota) e registre a decisão em `02-decisoes.md`.

## 1. Implementar, nesta ordem

1. **Modelo** (`app/domain/models.py`): `class Product(BaseModel)` com `__tablename__`, colunas,
   índice parcial de unicidade e FK do dono — **e o lado inverso** em `User`
   (`products: Mapped[list["Product"]] = relationship(back_populates="owner")`): o
   `back_populates` exige os dois lados.
2. **Migração**: siga a skill `/migracao` (rebuild do `api`, `--rev-id` sequencial, revisão do
   arquivo). A aplicação ainda sobe, porque nada mais referencia a entidade neste ponto.
3. **Repositório** (`app/repository/sql/product_repository.py`): `BaseRepository[Product]`;
   `loader_options` se algum schema de saída aninhar relacionamento.
4. **Serviço** (`app/service/product_service.py`): mensagens de 409 e 404 em pt-BR, `owned` e,
   para cada texto obrigatório, `_clean_text` no `before_create` e no `before_update`, como em
   `ItemService`.
5. **Schemas** (`app/schemas/product_schema.py`): os cinco, com limites iguais ao `String(n)` das
   colunas e o validador "ao menos um campo" no Update.
6. **Container** (`app/ext/container.py`): `product_repository` e `product_service`.
7. **Permissões** (`app/authz.py`): os quatro membros em `Permission` e as concessões nos papéis.
8. **Rota** (`app/blueprint/restapi/v1/product.py`): copie `item.py` inteiro (View base com o
   piso, Collection e Resource) e troque os nomes. Cada método de escrita mantém o seu
   `@permission_required`. Descrição do `Blueprint` e docstrings em pt-BR, escritas para o cliente
   da API (são publicadas no OpenAPI).
9. **Ativar** (`settings.toml`): `"blueprint.restapi.v1.product"` em `API_V1_ROUTES` do
   `[default]` **e** do perfil do processo que serve a rota (`[api]`, para as de negócio; 03
   §1.9). O `tests/unit/test_composicao.py` reprova se ela estiver numa lista só.
10. **Testes** (`tests/test_product.py`, a partir de `test_item.py`). Mínimo: criação; listagem
    paginada; 409 de duplicado; 422 de entrada inválida; exclusão lógica sumindo das consultas;
    404 para recurso de outro dono; 403 com `Role.LEITOR` em **cada** escrita (POST com
    `login(role=Role.LEITOR)`; PATCH e DELETE criando como `usuario` e rebaixando com `set_role`);
    e, com os escopos padrão, gerente lê mas não altera o de outro dono (molde em `test_authz.py`).

Itere com `/verificar testes tests/test_product.py`.

## 2. Fechar

1. `/verificar` (tudo): inclui `alembic check` e o ciclo de migração.
2. Agente `revisor-arquitetura` sobre o diff; corrija os bloqueantes e rode `/verificar` de novo.
3. Documentação: se a entidade trouxe algo que a receita não cobre, atualize
   `documentacao/03-como-estender.md`. Não remova `Item` sem pedido explícito.
4. Relate os arquivos criados e alterados, a migração gerada, o resultado do `/verificar`, os
   achados da revisão e o que ficou de fora.
