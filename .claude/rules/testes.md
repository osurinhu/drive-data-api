---
paths:
  - "tests/**/*.py"
---

# Testes (`tests/`)

pytest contra PostgreSQL real, nunca SQLite. Rode com `/verificar testes <args>`. Moldes:
`tests/test_item.py` (CRUD) e `tests/test_authz.py` (papéis). Receita: 03-como-estender §1.10.

- Fixtures (`tests/conftest.py`): `client`; `login(name, password, role)` → `(user_dict, headers)`;
  `headers` (usuário comum já autenticado); `make_user(...)` → dict; `set_role(nome, papel)` troca o
  papel sem reemitir o token; `sql_log` (lista do SQL emitido — chame `.clear()` depois do preparo).
- As tabelas são esvaziadas após cada teste: a ordem não importa, mas não presuma ids.
- O banco de testes nasce de `create_all()`, não das migrações: teste verde não prova que a
  migração existe. Por isso o `/verificar` também roda o `alembic check`.
- Nome do teste em pt-BR descrevendo o comportamento
  (`test_nome_duplicado_para_o_mesmo_dono_retorna_409`); docstring quando o porquê não for óbvio.
  Preparo, ação e verificação separados por linha em branco.
- Parâmetro de fixture não usado reprova no ruff (`ARG001`).
- Cobertura mínima de recurso novo: criação, listagem paginada, 409 de duplicado, 422 de entrada
  inválida, exclusão lógica sumindo das consultas, **404 para recurso de outro dono** e **403 com
  `Role.LEITOR` em cada operação de escrita** — na criação, `login(role=Role.LEITOR)`; nas escritas
  sobre registro existente (PATCH, DELETE, ações como o `arquivar` da §2), crie como `usuario`,
  rebaixe com `set_role(nome, Role.LEITOR)` e espere 403. É o único teste que pega um
  `@permission_required` de escrita esquecido.
- `test_contracts.py` guarda contratos que nenhum teste funcional pegaria. Nunca o afrouxe para
  passar: corrija a causa. Só altere `ROTAS_PUBLICAS` ao abrir uma rota de propósito.
- `test_write_cost.py` fixa contagens medidas; mudar um número exige medir (`/medir-consultas`) e
  explicar no cabeçalho. `tests/test_zz_medir.py` é a medição temporária daquela skill: nunca a
  deixe no commit, e o `/verificar` (tudo) recusa rodar com ela presente.
