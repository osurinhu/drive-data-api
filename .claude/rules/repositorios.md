---
paths:
  - "app/repository/sql/**/*.py"
---

# Repositórios SQL (`app/repository/sql/`)

Única parte do projeto que monta SQL. **Não pode:** conter regra de negócio nem decidir
permissão. Base: `app/repository/sql/base_repository.py`. Receitas: 03-como-estender §1.3 e §3.
A camada tem uma subpasta por tecnologia (02-decisoes, "Um nível de pasta por papel, outro por
tecnologia"). Estas regras valem só para `sql/`.

- Repositório concreto é uma linha: `class XRepository(BaseRepository[X])` com
  `super().__init__(X)`. O `[X]` faz `get_by_id()` ser tipado como `X | None`.
- Consulta que o CRUD genérico não cobre vira método aqui — nunca no serviço. Parta de
  `self._select()`, que já aplica `deleted = false` e os `loader_options`; montando do zero,
  filtre `self.model.deleted.is_(False)` você mesmo.
- Schema de saída que aninha relacionamento exige `loader_options = (selectinload(X.rel),)`;
  sem isso, listar 20 registros dispara 21 consultas. Meça com `/medir-consultas`, usando
  registros de **donos diferentes**: com um dono só, ele já está na sessão e o N+1 não aparece.
- **Sem `refresh()` depois do `flush()`**: o commit do serviço já expira os atributos, e
  `tests/test_write_cost.py` reprova se as contagens subirem (02-decisoes, "Sem `refresh()`").
- Filtro parcial vazio é ignorado (`_apply_like` pula `None` e `""`): mantenha isso em filtros novos.
- Paginação por OFFSET emite um COUNT por chamada e fica lenta em páginas altas; para listas
  muito grandes, prefira paginação por cursor num método da subclasse.
- O mypy cobre esta camada.
