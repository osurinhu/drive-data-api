---
paths:
  - "app/domain/**/*.py"
---

# Domínio (`app/domain/`)

O pacote tem duas partes (02-decisoes, "Na raiz de `app/`, só o `app.py`").

- **O ORM** (`base.py`, `models.py`): regras em `rules/modelos-e-migracoes.md`.
- **O vocabulário** (todo o resto: `authz.py`, `vin.py`, `metric.py`, `ports.py`, `contracts/`, este com
  regra própria em `rules/contratos.md`): folhas que qualquer
  camada e qualquer processo importa. Uma folha só importa a stdlib, o marshmallow e outras
  folhas. É uma lista de permitidos: nem o ORM, nem outra camada, nem biblioteca nenhuma além
  dessas. O ORM importa só o SQLAlchemy e o próprio domínio.
- Vocabulário novo (um tipo, uma regra de valor, uma porta, um contrato) nasce aqui, nunca
  solto na raiz de `app/`, onde só mora o `app.py`.
- `__init__.py` sem import: o consumer importa `domain.ports` e não pode carregar o SQLAlchemy.
- `tests/unit/test_fronteiras.py` lê os imports com `ast` e confere as três regras (a folha, o
  ORM e o `__init__.py` sem import). Só os nomes do conjunto `ORM` escapam da regra da folha:
  acrescentar um nome ali, ou uma biblioteca aos permitidos, é decisão de arquitetura, não
  conserto de teste.
- O mypy cobre o pacote inteiro.
