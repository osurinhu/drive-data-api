"""Veículos: cadastro, VIN, unicidade global, ownership, papéis e a posse para o envio."""

import pytest

from authz import Role
from ports import AuthorizedVehicle, SenderRelation
from service.base_service import ANY_OWNER
from service.errors import BusinessRuleError

VEHICLE_URL = "/api/v1/vehicle/"
VIN = "93YBSR7RHEJ298116"
OUTRO_VIN = "9BWZZZ377VT004251"


def _cadastrar(client, headers, vin: str = VIN, nickname: str | None = None):
    payload = {"vin": vin}
    if nickname is not None:
        payload["nickname"] = nickname
    return client.post(VEHICLE_URL, json=payload, headers=headers)


def _id(response) -> int:
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


# --- Criação e VIN ------------------------------------------------------------


def test_cadastrar_veiculo_faz_do_usuario_o_gestor(client, login):
    user, headers = login()

    response = _cadastrar(client, headers, nickname="Sandero")

    assert response.status_code == 201
    body = response.get_json()
    assert body["vin"] == VIN
    assert body["nickname"] == "Sandero"
    assert body["owner_id"] == user["id"]


def test_apelido_e_opcional(client, headers):
    response = _cadastrar(client, headers)

    assert response.status_code == 201
    assert response.get_json()["nickname"] is None


def test_vin_e_gravado_na_forma_canonica(client, headers):
    response = _cadastrar(client, headers, vin="  93ybsr7rhej298116 ")

    assert response.status_code == 201
    assert response.get_json()["vin"] == VIN


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({}, id="sem VIN"),
        pytest.param({"vin": "93YBSR7RHEJ29811O"}, id="VIN com O"),
        pytest.param({"vin": "93YBSR7RHEJ29811"}, id="VIN curto"),
        pytest.param({"vin": VIN, "nickname": "x" * 51}, id="apelido longo"),
    ],
)
def test_entrada_invalida_retorna_422(client, headers, payload):
    response = client.post(VEHICLE_URL, json=payload, headers=headers)

    assert response.status_code == 422


def test_o_servico_recusa_vin_invalido_de_quem_nao_passa_por_schema(app, make_user):
    user = make_user()

    with app.app_context(), pytest.raises(BusinessRuleError):
        app.container.vehicle_service().create(user["id"], vin="93YBSR7RHEJ29811O")


# --- Unicidade ------------------------------------------------------------------


def test_vin_repetido_para_o_mesmo_dono_retorna_409(client, headers):
    _cadastrar(client, headers)

    response = _cadastrar(client, headers)

    assert response.status_code == 409
    assert response.get_json()["message"] == "Este VIN já está cadastrado."


def test_vin_e_unico_entre_todos_os_donos(client, login):
    """Diferente do nome do item: dois cadastros ativos do mesmo VIN seriam dois
    gestores para o mesmo carro. Em minúsculas, para provar que a unicidade vale
    sobre a forma canônica."""
    _, headers_ana = login(name="ana")
    _, headers_bruno = login(name="bruno")
    _cadastrar(client, headers_ana)

    response = _cadastrar(client, headers_bruno, vin=VIN.lower())

    assert response.status_code == 409


def test_vin_fica_livre_depois_da_exclusao(client, login):
    """O índice único parcial (`WHERE deleted = false`) em ação."""
    _, headers_ana = login(name="ana")
    _, headers_bruno = login(name="bruno")
    vehicle_id = _id(_cadastrar(client, headers_ana))
    client.delete(f"{VEHICLE_URL}{vehicle_id}", headers=headers_ana)

    response = _cadastrar(client, headers_bruno)

    assert response.status_code == 201


# --- Leitura --------------------------------------------------------------------


def test_buscar_veiculo_por_id(client, headers):
    vehicle_id = _id(_cadastrar(client, headers))

    response = client.get(f"{VEHICLE_URL}{vehicle_id}", headers=headers)

    assert response.status_code == 200
    assert response.get_json()["vin"] == VIN


def test_buscar_veiculo_inexistente_retorna_404(client, headers):
    response = client.get(f"{VEHICLE_URL}999999", headers=headers)

    assert response.status_code == 404
    assert response.get_json()["message"] == "Veículo não encontrado."


def test_listagem_usa_o_envelope_de_paginacao(client, headers):
    for vin in (VIN, OUTRO_VIN, "1HGCM82633A004352"):
        _cadastrar(client, headers, vin=vin)

    body = client.get(f"{VEHICLE_URL}?page=1&per_page=2", headers=headers).get_json()

    assert len(body["items"]) == 2
    assert body["total_of_items"] == 3
    assert body["has_next"] is True


def test_filtro_por_vin_e_parcial_e_ignora_caixa(client, headers):
    _cadastrar(client, headers, vin=VIN)
    _cadastrar(client, headers, vin=OUTRO_VIN)

    body = client.get(f"{VEHICLE_URL}?vin=rhej298", headers=headers).get_json()

    assert body["total_of_items"] == 1
    assert body["items"][0]["vin"] == VIN


# --- Atualização ----------------------------------------------------------------


def test_atualizar_o_apelido(client, headers):
    vehicle_id = _id(_cadastrar(client, headers, nickname="antes"))

    response = client.patch(
        f"{VEHICLE_URL}{vehicle_id}", json={"nickname": "depois"}, headers=headers
    )

    assert response.status_code == 200
    assert response.get_json()["nickname"] == "depois"
    assert response.get_json()["vin"] == VIN


def test_atualizacao_aceita_limpar_o_apelido(client, headers):
    vehicle_id = _id(_cadastrar(client, headers, nickname="antes"))

    response = client.patch(f"{VEHICLE_URL}{vehicle_id}", json={"nickname": None}, headers=headers)

    assert response.status_code == 200
    assert response.get_json()["nickname"] is None


@pytest.mark.parametrize(
    "payload",
    [pytest.param({}, id="vazio"), pytest.param({"vin": OUTRO_VIN}, id="troca de VIN")],
)
def test_atualizacao_invalida_retorna_422(client, headers, payload):
    vehicle_id = _id(_cadastrar(client, headers))

    response = client.patch(f"{VEHICLE_URL}{vehicle_id}", json=payload, headers=headers)

    assert response.status_code == 422
    assert client.get(f"{VEHICLE_URL}{vehicle_id}", headers=headers).get_json()["vin"] == VIN


def test_o_servico_recusa_trocar_o_vin(app, make_user):
    """O schema do PATCH nem aceita o campo; o serviço guarda a regra para quem
    chama sem passar por ele."""
    user = make_user()

    with app.app_context():
        service = app.container.vehicle_service()
        vehicle = service.create(user["id"], vin=VIN)
        with pytest.raises(BusinessRuleError):
            service.update(vehicle.id, ANY_OWNER, vin=OUTRO_VIN)


# --- Exclusão lógica ------------------------------------------------------------


def test_exclusao_remove_o_veiculo_das_consultas(client, headers):
    vehicle_id = _id(_cadastrar(client, headers))

    assert client.delete(f"{VEHICLE_URL}{vehicle_id}", headers=headers).status_code == 200
    assert client.get(f"{VEHICLE_URL}{vehicle_id}", headers=headers).status_code == 404
    assert client.get(VEHICLE_URL, headers=headers).get_json()["total_of_items"] == 0


# --- Ownership e escopo ---------------------------------------------------------


def test_veiculo_de_outro_usuario_responde_404(client, login):
    _, headers_ana = login(name="ana")
    _, headers_bruno = login(name="bruno")
    url = f"{VEHICLE_URL}{_id(_cadastrar(client, headers_ana))}"

    assert client.get(url, headers=headers_bruno).status_code == 404
    assert client.patch(url, json={"nickname": "x"}, headers=headers_bruno).status_code == 404
    assert client.delete(url, headers=headers_bruno).status_code == 404


def test_listagem_mostra_somente_os_veiculos_do_dono(client, login):
    _, headers_ana = login(name="ana")
    _, headers_bruno = login(name="bruno")
    _cadastrar(client, headers_ana, vin=VIN)
    _cadastrar(client, headers_bruno, vin=OUTRO_VIN)

    body = client.get(VEHICLE_URL, headers=headers_ana).get_json()

    assert body["total_of_items"] == 1
    assert body["items"][0]["vin"] == VIN


def test_administrador_le_e_altera_o_veiculo_de_outro_dono(client, login):
    """O admin resolve disputas de VIN: tem os dois escopos de veículo."""
    _, headers_ana = login(name="ana")
    _, headers_admin = login(name="raiz", role=Role.ADMIN)
    url = f"{VEHICLE_URL}{_id(_cadastrar(client, headers_ana))}"

    assert client.get(VEHICLE_URL, headers=headers_admin).get_json()["total_of_items"] == 1
    response = client.patch(url, json={"nickname": "do admin"}, headers=headers_admin)
    assert response.status_code == 200
    assert client.delete(url, headers=headers_admin).status_code == 200


# --- 403: a silver não mexe em veículo ------------------------------------------


def test_silver_nao_cadastra_nem_lista_veiculo(client, login):
    _, headers = login(name="silver-etl", role=Role.SILVER)

    assert _cadastrar(client, headers).status_code == 403
    assert client.get(VEHICLE_URL, headers=headers).status_code == 403


def test_dono_rebaixado_a_silver_nao_altera_nem_exclui(client, login, set_role):
    """O dono sem permissão leva o 403, nunca o 404. Como a `silver` nem lê
    veículo, o 403 sai do piso da classe: quem garante o decorator de cada escrita
    é o `test_toda_escrita_protegida_declara_a_propria_permissao`."""
    ana, headers = login(name="ana")
    vehicle_id = _id(_cadastrar(client, headers, nickname="da ana"))
    set_role(ana["name"], Role.SILVER)

    patch = client.patch(f"{VEHICLE_URL}{vehicle_id}", json={"nickname": "x"}, headers=headers)
    delete = client.delete(f"{VEHICLE_URL}{vehicle_id}", headers=headers)

    assert patch.status_code == 403
    assert delete.status_code == 403
    set_role(ana["name"], Role.USUARIO)
    body = client.get(f"{VEHICLE_URL}{vehicle_id}", headers=headers).get_json()
    assert body["nickname"] == "da ana"


# --- Posse para o envio (VehicleRepository.authorized_vins) ---------------------


def _autorizados(app, user_id: int, vins) -> dict:
    with app.app_context():
        return app.container.vehicle_repository().authorized_vins(user_id, vins)


def test_o_gestor_pode_enviar_pelos_proprios_veiculos_ativos(app, client, login):
    ana, headers = login(name="ana")
    vehicle_id = _id(_cadastrar(client, headers))

    autorizados = _autorizados(app, ana["id"], [VIN, OUTRO_VIN, VIN])

    assert autorizados == {
        VIN: AuthorizedVehicle(vehicle_id=vehicle_id, relation=SenderRelation.OWNER)
    }


def test_veiculo_de_outro_dono_ou_excluido_nao_autoriza(app, client, login):
    ana, headers_ana = login(name="ana")
    _, headers_bruno = login(name="bruno")
    _cadastrar(client, headers_bruno, vin=OUTRO_VIN)
    excluido = _id(_cadastrar(client, headers_ana, vin=VIN))
    client.delete(f"{VEHICLE_URL}{excluido}", headers=headers_ana)

    assert _autorizados(app, ana["id"], [VIN, OUTRO_VIN]) == {}


def test_lista_vazia_nao_consulta_o_banco(app, make_user, sql_log):
    user = make_user()
    sql_log.clear()

    assert _autorizados(app, user["id"], []) == {}
    assert sql_log == []
