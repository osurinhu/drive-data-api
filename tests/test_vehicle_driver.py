"""Motoristas: o sub-recurso que herda a posse do veículo, a visão do motorista, os
papéis e a posse para o envio."""

import pytest

from domain.authz import Role
from domain.ports import AuthorizedVehicle, SenderRelation
from service.vehicle_driver_service import (
    NOT_DRIVING_MESSAGE,
    OWNER_AS_DRIVER_MESSAGE,
    UNKNOWN_USER_MESSAGE,
)

VEHICLE_URL = "/api/v1/vehicle/"
DRIVING_URL = "/api/v1/vehicle/driving"
VIN = "93YBSR7RHEJ298116"
OUTRO_VIN = "9BWZZZ377VT004251"
TERCEIRO_VIN = "1HGCM82633A004352"


def _cadastrar(client, headers, vin: str = VIN, nickname: str | None = None) -> int:
    response = client.post(VEHICLE_URL, json={"vin": vin, "nickname": nickname}, headers=headers)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


def _motoristas(vehicle_id: int) -> str:
    return f"{VEHICLE_URL}{vehicle_id}/drivers"


def _adicionar(client, headers, vehicle_id: int, user_name: str):
    return client.post(_motoristas(vehicle_id), json={"user_name": user_name}, headers=headers)


def _nomes(client, headers, vehicle_id: int) -> list[str]:
    body = client.get(_motoristas(vehicle_id), headers=headers).get_json()
    return [item["driver_name"] for item in body["items"]]


# --- O gestor adiciona ----------------------------------------------------------


def test_o_gestor_adiciona_um_motorista_pelo_login(client, login, make_user):
    _, headers = login(name="ana")
    joao = make_user(name="joao")
    vehicle_id = _cadastrar(client, headers)

    response = _adicionar(client, headers, vehicle_id, "joao")

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert set(body) == {"vehicle_id", "driver_id", "driver_name", "created_at"}
    assert body["vehicle_id"] == vehicle_id
    assert body["driver_id"] == joao["id"]
    assert body["driver_name"] == "joao"


def test_o_login_e_aparado_nas_pontas(client, login, make_user):
    _, headers = login(name="ana")
    make_user(name="joao")
    vehicle_id = _cadastrar(client, headers)

    assert _adicionar(client, headers, vehicle_id, "  joao ").status_code == 201


def test_login_inexistente_responde_404(client, login):
    _, headers = login(name="ana")
    vehicle_id = _cadastrar(client, headers)

    response = _adicionar(client, headers, vehicle_id, "ninguem")

    assert response.status_code == 404
    assert response.get_json()["message"] == UNKNOWN_USER_MESSAGE


def test_o_gestor_nao_e_motorista_do_proprio_veiculo(client, login):
    """Ele já envia como `owner`: o vínculo seria um segundo caminho para a mesma
    autorização, e a remoção dele não tiraria nada."""
    _, headers = login(name="ana")
    vehicle_id = _cadastrar(client, headers)

    response = _adicionar(client, headers, vehicle_id, "ana")

    assert response.status_code == 422
    assert response.get_json()["message"] == OWNER_AS_DRIVER_MESSAGE


def test_motorista_repetido_responde_409(client, login, make_user):
    _, headers = login(name="ana")
    make_user(name="joao")
    vehicle_id = _cadastrar(client, headers)
    _adicionar(client, headers, vehicle_id, "joao")

    response = _adicionar(client, headers, vehicle_id, "joao")

    assert response.status_code == 409
    assert response.get_json()["message"] == "Esta pessoa já é motorista deste veículo."


def test_o_motorista_removido_pode_voltar(client, login, make_user):
    """O índice único parcial (`WHERE deleted = false`) em ação."""
    _, headers = login(name="ana")
    joao = make_user(name="joao")
    vehicle_id = _cadastrar(client, headers)
    _adicionar(client, headers, vehicle_id, "joao")
    client.delete(f"{_motoristas(vehicle_id)}/{joao['id']}", headers=headers)

    assert _adicionar(client, headers, vehicle_id, "joao").status_code == 201
    assert _nomes(client, headers, vehicle_id) == ["joao"]


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="sem login"),
        pytest.param({"user_name": ""}, id="login vazio"),
        pytest.param({"user_name": "   "}, id="só espaços"),
        pytest.param({"user_name": "x" * 51}, id="login longo"),
        pytest.param({"user_name": "joao", "papel": "x"}, id="campo desconhecido"),
    ],
)
def test_entrada_invalida_responde_422(client, login, payload):
    _, headers = login(name="ana")
    vehicle_id = _cadastrar(client, headers)

    response = client.post(_motoristas(vehicle_id), json=payload, headers=headers)

    assert response.status_code == 422


# --- O gestor lista e remove ----------------------------------------------------


def test_a_lista_do_gestor_traz_so_os_motoristas_ativos(client, login, make_user):
    _, headers = login(name="ana")
    maria = make_user(name="maria")
    for nome in ("joao", "pedro"):
        make_user(name=nome)
    vehicle_id = _cadastrar(client, headers)
    for nome in ("joao", "maria", "pedro"):
        _adicionar(client, headers, vehicle_id, nome)
    client.delete(f"{_motoristas(vehicle_id)}/{maria['id']}", headers=headers)

    pagina = client.get(f"{_motoristas(vehicle_id)}?per_page=1", headers=headers).get_json()

    assert pagina["total_of_items"] == 2
    assert pagina["has_next"] is True
    assert _nomes(client, headers, vehicle_id) == ["joao", "pedro"]


def test_remover_o_motorista(client, login, make_user):
    _, headers = login(name="ana")
    joao = make_user(name="joao")
    vehicle_id = _cadastrar(client, headers)
    _adicionar(client, headers, vehicle_id, "joao")
    url = f"{_motoristas(vehicle_id)}/{joao['id']}"

    response = client.delete(url, headers=headers)
    de_novo = client.delete(url, headers=headers)

    assert response.status_code == 200
    assert response.get_json()["driver_id"] == joao["id"]
    assert _nomes(client, headers, vehicle_id) == []
    assert de_novo.status_code == 404
    assert de_novo.get_json()["message"] == "Motorista não encontrado."


# --- A posse é a do veículo -----------------------------------------------------


def test_motoristas_do_veiculo_de_outro_gestor_respondem_404(client, login, make_user):
    _, headers_ana = login(name="ana")
    _, headers_bruno = login(name="bruno")
    joao = make_user(name="joao")
    vehicle_id = _cadastrar(client, headers_ana)
    _adicionar(client, headers_ana, vehicle_id, "joao")

    respostas = [
        client.get(_motoristas(vehicle_id), headers=headers_bruno),
        _adicionar(client, headers_bruno, vehicle_id, "bruno"),
        client.delete(f"{_motoristas(vehicle_id)}/{joao['id']}", headers=headers_bruno),
    ]

    assert [r.status_code for r in respostas] == [404, 404, 404]
    assert {r.get_json()["message"] for r in respostas} == {"Veículo não encontrado."}
    assert _nomes(client, headers_ana, vehicle_id) == ["joao"]


def test_o_motorista_nao_ve_nem_mexe_nos_outros_motoristas(client, login, make_user):
    """Ser motorista não é ser gestor: para ele, os motoristas do veículo são do outro."""
    _, headers_ana = login(name="ana")
    _, headers_joao = login(name="joao")
    make_user(name="maria")
    vehicle_id = _cadastrar(client, headers_ana)
    _adicionar(client, headers_ana, vehicle_id, "joao")

    assert client.get(_motoristas(vehicle_id), headers=headers_joao).status_code == 404
    assert _adicionar(client, headers_joao, vehicle_id, "maria").status_code == 404


def test_nenhuma_operacao_alcanca_o_vinculo_de_outro_par(client, login, make_user):
    """O vínculo é o par (veículo, motorista). A lista e a saída do joão filtram por ele, e a
    remoção da ana, pelo veículo dela: nenhuma chega à maria no veículo do bruno."""
    _, headers_ana = login(name="ana")
    _, headers_bruno = login(name="bruno")
    _, headers_joao = login(name="joao")
    maria = make_user(name="maria")
    da_ana = _cadastrar(client, headers_ana, VIN)
    do_bruno = _cadastrar(client, headers_bruno, OUTRO_VIN)
    _adicionar(client, headers_ana, da_ana, "joao")
    _adicionar(client, headers_bruno, do_bruno, "maria")

    lista_do_joao = client.get(DRIVING_URL, headers=headers_joao).get_json()
    saida_do_joao = client.delete(f"{DRIVING_URL}/{do_bruno}", headers=headers_joao)
    remocao_da_ana = client.delete(f"{_motoristas(da_ana)}/{maria['id']}", headers=headers_ana)

    assert [item["vehicle_id"] for item in lista_do_joao["items"]] == [da_ana]
    assert (saida_do_joao.status_code, remocao_da_ana.status_code) == (404, 404)
    assert _nomes(client, headers_bruno, do_bruno) == ["maria"]
    assert _nomes(client, headers_ana, da_ana) == ["joao"]


def test_veiculo_inexistente_responde_404(client, headers):
    assert client.get(_motoristas(999999), headers=headers).status_code == 404


def test_administrador_gerencia_os_motoristas_de_qualquer_veiculo(client, login, make_user):
    """O admin resolve disputas: os escopos de veículo valem para os motoristas."""
    _, headers_ana = login(name="ana")
    _, headers_admin = login(name="raiz", role=Role.ADMIN)
    joao = make_user(name="joao")
    vehicle_id = _cadastrar(client, headers_ana)

    adicao = _adicionar(client, headers_admin, vehicle_id, "joao")
    lista = client.get(_motoristas(vehicle_id), headers=headers_admin)
    remocao = client.delete(f"{_motoristas(vehicle_id)}/{joao['id']}", headers=headers_admin)

    assert (adicao.status_code, lista.status_code, remocao.status_code) == (201, 200, 200)
    assert lista.get_json()["total_of_items"] == 1


# --- O motorista ----------------------------------------------------------------


def test_o_motorista_lista_os_veiculos_que_dirige(client, login):
    _, headers_ana = login(name="ana")
    _, headers_bruno = login(name="bruno")
    _, headers_joao = login(name="joao")
    da_ana = _cadastrar(client, headers_ana, VIN, nickname="Sandero")
    do_bruno = _cadastrar(client, headers_bruno, OUTRO_VIN)
    _cadastrar(client, headers_bruno, TERCEIRO_VIN)  # do bruno, mas o joao não dirige
    _adicionar(client, headers_ana, da_ana, "joao")
    _adicionar(client, headers_bruno, do_bruno, "joao")

    body = client.get(DRIVING_URL, headers=headers_joao).get_json()

    assert body["total_of_items"] == 2
    primeiro, segundo = body["items"]
    assert set(primeiro) == {"vehicle_id", "vin", "nickname", "owner_name", "created_at"}
    assert (primeiro["vehicle_id"], primeiro["vin"]) == (da_ana, VIN)
    assert (primeiro["nickname"], primeiro["owner_name"]) == ("Sandero", "ana")
    assert (segundo["vehicle_id"], segundo["owner_name"]) == (do_bruno, "bruno")


def test_o_gestor_nao_aparece_como_motorista_dos_proprios_veiculos(client, login):
    _, headers = login(name="ana")
    _cadastrar(client, headers)

    assert client.get(DRIVING_URL, headers=headers).get_json()["total_of_items"] == 0


def test_o_motorista_se_desvincula(client, login):
    _, headers_ana = login(name="ana")
    _, headers_joao = login(name="joao")
    vehicle_id = _cadastrar(client, headers_ana)
    _adicionar(client, headers_ana, vehicle_id, "joao")

    response = client.delete(f"{DRIVING_URL}/{vehicle_id}", headers=headers_joao)
    de_novo = client.delete(f"{DRIVING_URL}/{vehicle_id}", headers=headers_joao)

    assert response.status_code == 200
    assert response.get_json()["vin"] == VIN
    assert client.get(DRIVING_URL, headers=headers_joao).get_json()["total_of_items"] == 0
    assert _nomes(client, headers_ana, vehicle_id) == []
    assert de_novo.status_code == 404
    assert de_novo.get_json()["message"] == NOT_DRIVING_MESSAGE


def test_desvincular_de_veiculo_que_nao_dirige_responde_404(client, login):
    """Nem o veículo do próprio gestor: ele não é motorista dele."""
    _, headers = login(name="ana")
    vehicle_id = _cadastrar(client, headers)

    assert client.delete(f"{DRIVING_URL}/{vehicle_id}", headers=headers).status_code == 404
    assert client.delete(f"{DRIVING_URL}/999999", headers=headers).status_code == 404


# --- A exclusão do veículo ------------------------------------------------------


def test_excluir_o_veiculo_desliga_os_motoristas(app, client, login):
    """Os vínculos não sobram para o próximo cadastro do mesmo VIN, nem na lista do
    motorista, nem na posse do envio."""
    _, headers_ana = login(name="ana")
    _, headers_bruno = login(name="bruno")
    joao, headers_joao = login(name="joao")
    vehicle_id = _cadastrar(client, headers_ana)
    _adicionar(client, headers_ana, vehicle_id, "joao")

    assert client.delete(f"{VEHICLE_URL}{vehicle_id}", headers=headers_ana).status_code == 200
    do_bruno = _cadastrar(client, headers_bruno)

    assert client.get(DRIVING_URL, headers=headers_joao).get_json()["total_of_items"] == 0
    assert _nomes(client, headers_bruno, do_bruno) == []
    assert _autorizados(app, joao["id"], [VIN]) == {}


# --- 403: só quem lê e altera veículo mexe em motorista -------------------------


def test_silver_nao_mexe_em_motorista(client, login):
    """O 403 vem antes do 404: o id do veículo nem precisa existir."""
    _, headers = login(name="silver-etl", role=Role.SILVER)

    respostas = [
        client.get(_motoristas(1), headers=headers),
        _adicionar(client, headers, 1, "joao"),
        client.post(_motoristas(1), json={}, headers=headers),  # 403 antes do 422
        client.delete(f"{_motoristas(1)}/1", headers=headers),
        client.get(DRIVING_URL, headers=headers),
        client.delete(f"{DRIVING_URL}/1", headers=headers),
    ]

    assert [r.status_code for r in respostas] == [403] * 6


def test_gestor_rebaixado_a_silver_nao_adiciona_nem_remove(client, login, make_user, set_role):
    """O dono sem permissão leva o 403, nunca o 404. Como a `silver` nem lê veículo, o
    403 sai do piso da classe: quem garante o decorator de cada escrita é o
    `test_toda_escrita_protegida_declara_a_propria_permissao`."""
    ana, headers = login(name="ana")
    joao = make_user(name="joao")
    make_user(name="maria")
    vehicle_id = _cadastrar(client, headers)
    _adicionar(client, headers, vehicle_id, "joao")
    set_role(ana["name"], Role.SILVER)

    adicao = _adicionar(client, headers, vehicle_id, "maria")
    remocao = client.delete(f"{_motoristas(vehicle_id)}/{joao['id']}", headers=headers)

    assert (adicao.status_code, remocao.status_code) == (403, 403)
    set_role(ana["name"], Role.USUARIO)
    assert _nomes(client, headers, vehicle_id) == ["joao"]


def test_motorista_rebaixado_a_silver_nao_se_desvincula(client, login, set_role):
    _, headers_ana = login(name="ana")
    joao, headers_joao = login(name="joao")
    vehicle_id = _cadastrar(client, headers_ana)
    _adicionar(client, headers_ana, vehicle_id, "joao")
    set_role(joao["name"], Role.SILVER)

    response = client.delete(f"{DRIVING_URL}/{vehicle_id}", headers=headers_joao)

    assert response.status_code == 403
    assert _nomes(client, headers_ana, vehicle_id) == ["joao"]


# --- Posse para o envio (VehicleRepository.authorized_vins) ---------------------


def _autorizados(app, user_id: int, vins) -> dict:
    with app.app_context():
        return app.container.vehicle_repository().authorized_vins(user_id, vins)


def test_o_motorista_ativo_envia_como_driver(app, client, login, make_user):
    _, headers = login(name="ana")
    joao = make_user(name="joao")
    vehicle_id = _cadastrar(client, headers)
    _adicionar(client, headers, vehicle_id, "joao")

    assert _autorizados(app, joao["id"], [VIN]) == {
        VIN: AuthorizedVehicle(vehicle_id=vehicle_id, relation=SenderRelation.DRIVER)
    }


def test_o_motorista_removido_ou_que_saiu_nao_envia(app, client, login):
    _, headers_ana = login(name="ana")
    _, headers_bruno = login(name="bruno")
    joao, headers_joao = login(name="joao")
    da_ana = _cadastrar(client, headers_ana, VIN)
    do_bruno = _cadastrar(client, headers_bruno, OUTRO_VIN)
    _adicionar(client, headers_ana, da_ana, "joao")
    _adicionar(client, headers_bruno, do_bruno, "joao")

    client.delete(f"{_motoristas(da_ana)}/{joao['id']}", headers=headers_ana)
    client.delete(f"{DRIVING_URL}/{do_bruno}", headers=headers_joao)

    assert _autorizados(app, joao["id"], [VIN, OUTRO_VIN]) == {}


def test_o_vinculo_com_um_veiculo_nao_autoriza_outro(app, client, login, make_user):
    _, headers_ana = login(name="ana")
    _, headers_bruno = login(name="bruno")
    joao = make_user(name="joao")
    da_ana = _cadastrar(client, headers_ana, VIN)
    _cadastrar(client, headers_bruno, OUTRO_VIN)
    _adicionar(client, headers_ana, da_ana, "joao")

    assert set(_autorizados(app, joao["id"], [VIN, OUTRO_VIN])) == {VIN}


def test_um_lote_com_os_dois_vinculos_custa_uma_consulta(app, client, login, sql_log):
    """O joão é gestor de um carro e motorista de outro: um VIN de cada vínculo, mais
    um alheio, numa consulta só."""
    _, headers_ana = login(name="ana")
    joao, headers_joao = login(name="joao")
    da_ana = _cadastrar(client, headers_ana, VIN)
    do_joao = _cadastrar(client, headers_joao, OUTRO_VIN)
    _adicionar(client, headers_ana, da_ana, "joao")
    sql_log.clear()

    autorizados = _autorizados(app, joao["id"], [VIN, OUTRO_VIN, TERCEIRO_VIN])

    assert autorizados == {
        VIN: AuthorizedVehicle(vehicle_id=da_ana, relation=SenderRelation.DRIVER),
        OUTRO_VIN: AuthorizedVehicle(vehicle_id=do_joao, relation=SenderRelation.OWNER),
    }
    assert len(sql_log) == 1
