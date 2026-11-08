---
name: medir-consultas
description: Mede quantos comandos SQL uma operação emite — rota inteira ou chamada de serviço —, com a fixture sql_log, e compara a mesma listagem com 1 e com 20 registros para detectar N+1. Use antes de otimizar consulta, repositório ou serialização ("medido, não suposto"), ao suspeitar de N+1 ou lentidão, ao aninhar relacionamento num schema de saída, ao acrescentar loader_options, e quando o tests/test_write_cost.py ou o tests/test_read_cost.py reprovar.
argument-hint: "<operação a medir, ex.: GET /vehicle/ com 20 veículos>"
allowed-tools: Bash(bash .claude/skills/verificar/verificar.sh *)
---

# /medir-consultas — $ARGUMENTS

O projeto só otimiza o que mediu. A decisão "Sem `refresh()` depois do `flush()`", em
`documentacao/02-decisoes.md`, refutou a hipótese original justamente medindo. O padrão é um teste
temporário com a fixture `sql_log`, que lista o SQL emitido. Ele roda pelo `/verificar` no modo
`medir`, que mostra a saída inteira.

## 1. Escrever a medição

Crie `tests/test_zz_medir.py` a partir do modelo abaixo e adapte a operação, os dados e o papel.
O nome é fixo: é ele que o modo `medir` roda e que o `/verificar` (tudo) recusa encontrar.

```python
"""Medição temporária do /medir-consultas — apague antes de concluir.

O `/verificar` (tudo) recusa rodar enquanto este arquivo existir.
"""

VEHICLE_URL = "/api/v1/vehicle/"
VIN = "93YBSR7RHEJ2981"  # + dois dígitos


def _relatorio(titulo: str, sql_log: list[str]) -> None:
    verbos: dict[str, int] = {}
    for comando in sql_log:
        verbo = comando.split(None, 1)[0].upper()
        verbos[verbo] = verbos.get(verbo, 0) + 1
    print(f"\n== {titulo}: {len(sql_log)} comandos {verbos}")
    for comando in sql_log:
        print("    " + " ".join(comando.split())[:150])


def test_medir_listagem(client, login, sql_log):
    """A mesma requisição com 1 e com 20 registros: se o número de comandos
    cresce com os registros, há N+1."""
    _, headers = login(name="medidor")
    criados = 0
    for total in (1, 20):
        while criados < total:
            resposta = client.post(
                VEHICLE_URL, json={"vin": f"{VIN}{criados:02d}"}, headers=headers
            )
            assert resposta.status_code == 201
            criados += 1

        sql_log.clear()
        resposta = client.get(VEHICLE_URL, query_string={"per_page": 100}, headers=headers)

        assert resposta.status_code == 200
        _relatorio(f"GET {VEHICLE_URL} com {total} registro(s)", sql_log)


def test_medir_escrita_pela_rota(client, login, sql_log):
    _, headers = login(name="medidor")
    vehicle_id = client.post(
        VEHICLE_URL, json={"vin": f"{VIN}00"}, headers=headers
    ).get_json()["id"]

    sql_log.clear()
    resposta = client.patch(
        f"{VEHICLE_URL}{vehicle_id}", json={"nickname": "depois"}, headers=headers
    )

    assert resposta.status_code == 200
    _relatorio("PATCH pela rota (autenticação + escrita + serialização)", sql_log)


def test_medir_servico(app, sql_log, make_user):
    user = make_user()

    sql_log.clear()
    with app.app_context():
        app.container.vehicle_service().create(user["id"], vin=f"{VIN}00")

    _relatorio("VehicleService.create, sem HTTP", sql_log)
```

- Sempre chame `sql_log.clear()` depois do preparo: só a operação medida entra na conta.
- A rota inteira inclui a autenticação. O serviço, chamado direto (como em
  `tests/test_write_cost.py`), isola a lógica.
- N+1 de relacionamento muitos-para-um só aparece com **donos diferentes**. Se todos os registros
  forem do mesmo usuário, o dono já está na sessão (o `current_user` o carregou) e o SQLAlchemy o
  reaproveita sem consulta. Para medir esse caso, crie registros de vários usuários e liste como
  `admin`.

## 2. Rodar

```bash
bash .claude/skills/verificar/verificar.sh medir          # ou: medir -k listagem
```

## 3. Ler o resultado

Referência medida em 2026-11-08 sobre `Vehicle`:

| Operação | Comandos | De onde vem cada um |
|---|---|---|
| `GET /vehicle/` com 1 ou com 20 registros | 3 SELECT | usuário do token, página, contagem |
| `PATCH /vehicle/<id>` pela rota | 3 SELECT + 1 UPDATE | usuário do token, carga com posse, UPDATE, recarga depois do commit |
| `VehicleService.create` sem HTTP | 1 INSERT | `INSERT ... RETURNING`; nada de SELECT |
| listagem de 20 registros de 20 donos, por quem lê os de todos, com um campo de saída lendo `x.owner` (mutação de ensaio de 2026-11-06, no exemplo que saiu na Fase 3) | 23 SELECT | os 3 de sempre + 1 por dono: **N+1** |
| o mesmo, com 20 registros de um dono só | 3 SELECT | o dono já estava na sessão: o N+1 não aparece |
| os dois casos acima com `loader_options = (selectinload(X.owner),)` | 4 SELECT | os 3 + 1 do `selectinload`, constante. Com um dono só, custa 1 a mais |
| `GET /vehicle/<id>/drivers` com 20 motoristas (3 relações muitos-para-um), medido em 2026-11-08 | 24 sem eager load, 7 com `selectinload`, 4 com `joinedload` | o `selectinload` custa 1 por relação, mesmo com o objeto na sessão; o `joinedload` vem na página. Fixado em `tests/test_read_cost.py` |

- **Toda requisição autenticada custa 1 SELECT em `users`.** É deliberado (02-decisoes, "`current_user`
  custa uma consulta por requisição autenticada"), e acontece uma vez só, mesmo com dois
  `permission_required`.
- **Ler um campo depois do commit custa 1 SELECT.** O `commit()` expira os atributos
  (`expire_on_commit=True`); por isso o projeto tirou o `refresh()`.
- **A contagem cresce com o número de registros (ou de donos)?** É N+1. Declare
  `loader_options` no repositório (`rules/repositorios.md`): `joinedload(X.rel)` numa relação
  muitos-para-um (o esperado é nenhuma consulta a mais) e `selectinload(X.rel)` numa coleção
  (uma a mais, constante). Meça de novo.

## 4. Fechar

1. Apague `tests/test_zz_medir.py`. O `/verificar` (tudo) reprova enquanto ele existir.
2. Relate os números: antes e depois, quando houver otimização.
3. A otimização valeu e precisa ser protegida? Transforme a medição em teste de regressão, em
   `tests/test_write_cost.py` ou num teste de custo novo, com a tabela "antes × depois" no
   cabeçalho, e registre a decisão em `documentacao/02-decisoes.md` no formato "Medido, não
   suposto". Mudar um número que já existe exige medir e explicar o porquê no cabeçalho.
4. `/verificar` (tudo).
