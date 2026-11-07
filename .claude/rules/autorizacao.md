---
paths:
  - "app/domain/authz.py"
  - "app/ext/auth.py"
---

# Autorização (`app/domain/authz.py`, `app/ext/auth.py`)

`authz.py` é folha do domínio (ver `rules/dominio.md`): não importa nada do projeto, para que rota, serviço, modelo e schema
usem o mesmo vocabulário sem ciclo. Receitas: 03-como-estender §1.7 e §10; decisões em
02-decisoes, seção Segurança.

- Permissão é capacidade, no formato `recurso:verbo` (`ITEM_LER = "item:ler"`). Por recurso,
  quatro: `X_LER`, `X_ESCREVER`, `X_LER_DE_TODOS` e `X_ESCREVER_DE_TODOS`. Escopos de leitura e de
  escrita separados permitem o papel que vê tudo e altera só o próprio.
- Toda permissão é concedida a pelo menos um papel em `ROLE_PERMISSIONS`; senão a rota fica
  inacessível para todos, e `test_contracts.py` reprova. A hierarquia é inclusão de conjuntos
  (`_LEITOR ⊂ _USUARIO ⊂ _GERENTE ⊂ _ADMIN`); papel novo parte de um existente. A exceção é a
  conta de sistema `_SILVER`, ramo à parte e disjunto do `_ADMIN` (só lê o bronze; ninguém da
  hierarquia lê). Molde de migração de papel: `0002_papel_silver.py`.
- Papel novo, em quatro passos: membro em `Role`; entrada em `ROLE_PERMISSIONS`; **migração que
  troca o CHECK `ck_users_role`** (o autogenerate não detecta, e os testes passam porque usam
  `create_all`); `upgrade head` e `check`. O `--role` da CLI sai da enumeração sozinho.
- O papel mora só no banco (`current_user.role`). Nenhuma claim de papel no token; o `sub` é só o
  id, em string (RFC 7519).
- `permission_required` verifica o token uma vez por requisição (marca em `flask.g`), mesmo com
  piso na classe e exigência no método; `test_authz.py` fixa essa contagem.
- `current_owner_scope(permissão)` é o único lugar que dispensa o filtro de posse.
- 403 = falta permissão para a operação (decidido antes de consultar); 404 = recurso de outro dono.
