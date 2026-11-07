---
paths:
  - "app/domain/**/*.py"
  - "migrations/**/*.py"
  - "alembic.ini"
---

# Modelos e migrações (`app/domain/`, `migrations/`)

O modelo mapeia tabela para classe. **Não pode:** conter regra de negócio nem mixin de
serialização. Molde: `Item` em `app/domain/models.py`. Receitas: 03-como-estender §1.1, §1.2,
§6 e §10.3; decisões em 02-decisoes, seção Dados.

- Herde `BaseModel`: `id`, `created_at`, `last_update` (TIMESTAMPTZ) e `deleted` já vêm dele.
  Data é sempre `DateTime(timezone=True)`; `datetime` sem fuso reprova no ruff (`DTZ`).
- `String(n)` com tamanho explícito — o schema espelha o `n`.
- Único entre registros ativos = `Index(..., unique=True, postgresql_where=text("deleted = false"))`,
  com nome `uq_<tabela>_<colunas>_active`. Restrição única simples bloquearia o valor para sempre
  depois da exclusão lógica.
- FK com `ondelete` e `index=True`. `relationship(back_populates=...)` exige **os dois lados**: ao
  criar `Product.owner`, acrescente `User.products`.
- Enum em coluna: VARCHAR + CHECK como `ROLE_COLUMN_TYPE` (`native_enum=False`,
  `create_constraint=True`, `values_callable`). Sem os dois últimos, ou não há restrição ou se
  grava o nome do membro em vez do valor.
- **Toda mudança de modelo exige migração** (`/migracao`). A suíte usa `create_all()` e não
  percebe migração faltando; quem pega é o `alembic check` do CI e do `/verificar`.
- Migração: `--rev-id` sequencial de 4 dígitos (`0002_<slug>.py`); docstring no estilo da 0001;
  valores de enum escritos à mão, nunca importados de `authz`; nomes de restrição completos dentro
  de `op.f()`; `server_default` em coluna NOT NULL de tabela com dados; `downgrade()` espelhando o
  `upgrade()`. O ruff não checa `migrations/versions/`: revise com cuidado.
- Nunca edite migração que já saiu da sua máquina: crie outra.
- Mudança de CHECK (papel novo) não é detectada pelo autogenerate nem pelo `alembic check`:
  escreva a migração à mão (receita 10.3).
- O mypy cobre `app/domain/`.
