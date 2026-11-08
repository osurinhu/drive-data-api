"""Custo das listagens que mostram relacionamentos: o número de consultas não cresce
com a página.

Medido com o /medir-consultas em 2026-11-08, com 20 vínculos de 20 pessoas
diferentes (com uma só, ela já estaria na sessão, e o N+1 não apareceria): 20
motoristas de um veículo, e 20 veículos de 20 gestores com o mesmo motorista.

| listagem                    | sem eager load | `selectinload` | `joinedload` |
|-----------------------------|----------------|----------------|--------------|
| `GET /vehicle/<id>/drivers` | 24             | 7              | 4            |
| `GET /vehicle/driving`      | 43             | 6              | 3            |

Com 1 vínculo, sem eager load, as duas custam 5. Os 4 da lista do gestor: o usuário do
token, o veículo (a posse), a página (com o motorista na mesma consulta) e a contagem.
Os 3 da lista do motorista: o usuário do token, a página (com o veículo e o gestor) e a
contagem. O `selectinload` pagava uma consulta por relação, mesmo com o objeto já na
sessão; as três relações são muitos-para-um, e o `joinedload` não multiplica linhas.
"""

VEHICLE_URL = "/api/v1/vehicle/"
#: 20 VINs válidos e distintos: 17 caracteres, sem I, O e Q.
VINS = [f"9BWZZZ377VT0042{n:02d}" for n in range(20)]


def _motoristas(client, headers, vin: str) -> str:
    response = client.post(VEHICLE_URL, json={"vin": vin}, headers=headers)
    assert response.status_code == 201, response.get_json()
    return f"{VEHICLE_URL}{response.get_json()['id']}/drivers"


def test_a_lista_de_motoristas_custa_quatro_consultas_qualquer_que_seja_a_pagina(
    client, login, make_user, sql_log
):
    _, headers = login(name="gestora")
    url = _motoristas(client, headers, VINS[0])
    for n in range(20):
        make_user(name=f"motorista{n}")
        adicao = client.post(url, json={"user_name": f"motorista{n}"}, headers=headers)
        assert adicao.status_code == 201
    sql_log.clear()

    response = client.get(url, query_string={"per_page": 100}, headers=headers)

    assert len(response.get_json()["items"]) == 20
    assert len(sql_log) == 4, sql_log


def test_a_lista_do_motorista_custa_tres_consultas_qualquer_que_seja_a_pagina(
    client, login, sql_log
):
    _, do_motorista = login(name="motorista")
    for n in range(20):
        _, do_gestor = login(name=f"gestor{n}")
        url = _motoristas(client, do_gestor, VINS[n])
        adicao = client.post(url, json={"user_name": "motorista"}, headers=do_gestor)
        assert adicao.status_code == 201
    sql_log.clear()

    response = client.get(
        "/api/v1/vehicle/driving", query_string={"per_page": 100}, headers=do_motorista
    )

    assert len(response.get_json()["items"]) == 20
    assert len(sql_log) == 3, sql_log
