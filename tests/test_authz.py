"""Autorização por permissões.

O que estes testes fixam, e que nenhum teste de CRUD pegaria:

* a permissão decide a **operação** (403) e o ownership decide o **registro**
  (404) — duas respostas diferentes, de propósito;
* escopo de leitura e escopo de escrita são permissões separadas: a rota de escrita
  não pode aceitar o escopo de leitura;
* o papel é lido do banco, não do token.
"""

from domain.authz import ROLE_PERMISSIONS, Permission, Role, has_permission, permissions_of

VEHICLE_URL = "/api/v1/vehicle/"
VIN = "93YBSR7RHEJ298116"
OUTRO_VIN = "9BWZZZ377VT004251"


def _cadastrar(client, headers, vin: str = VIN) -> int:
    response = client.post(VEHICLE_URL, json={"vin": vin}, headers=headers)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


# --- O mapa ------------------------------------------------------------------


def test_os_papeis_sao_cumulativos():
    """A hierarquia existe por inclusão de conjuntos, não por peso numérico."""
    assert permissions_of(Role.USUARIO) < permissions_of(Role.ADMIN)


def test_a_silver_e_um_ramo_a_parte():
    """A conta da silver só lê o bronze: não tem permissão nenhuma da hierarquia,
    e nenhum papel dela lê o bronze, nem o admin (que contém todos os outros)."""
    assert permissions_of(Role.SILVER)
    assert permissions_of(Role.SILVER).isdisjoint(permissions_of(Role.ADMIN))


def test_has_permission_exige_todas_as_informadas():
    exigidas = (Permission.VEICULO_LER, Permission.VEICULO_ESCREVER_DE_TODOS)

    assert has_permission(Role.ADMIN, *exigidas)
    assert not has_permission(Role.USUARIO, *exigidas)


def test_papel_desconhecido_nao_tem_permissao_nenhuma():
    """Um papel que chegou do banco sem entrada no mapa precisa negar acesso —
    não virar KeyError, que o handler genérico transformaria em 500."""
    assert permissions_of("papel-que-nao-existe") == frozenset()  # type: ignore[arg-type]


# --- 403: a operação ----------------------------------------------------------


def test_sem_a_permissao_da_operacao_responde_403(client, login):
    _, headers = login(name="etl", role=Role.SILVER)

    response = client.post(VEHICLE_URL, json={"vin": VIN}, headers=headers)

    assert response.status_code == 403
    assert response.get_json()["message"] == "Seu perfil não permite esta operação."


def test_o_403_vem_antes_da_validacao_do_corpo(client, login):
    """Quem não pode fazer a operação não deve descobrir o formato esperado do
    corpo dela: 403, e não o 422 que o schema daria."""
    _, headers = login(name="etl", role=Role.SILVER)

    response = client.post(VEHICLE_URL, json={"campo": "invalido"}, headers=headers)

    assert response.status_code == 403


def test_sem_permissao_alguma_nenhuma_rota_de_veiculo_abre(client, login):
    """A `silver` não tem `veiculo:ler`, e o GET não tem decorator próprio: é o piso
    declarado na classe que nega."""
    _, headers = login(name="etl", role=Role.SILVER)

    assert client.get(VEHICLE_URL, headers=headers).status_code == 403
    assert client.get(f"{VEHICLE_URL}1", headers=headers).status_code == 403


def test_quem_so_le_o_veiculo_nao_escreve_nada(client, login, make_user, monkeypatch):
    """Nenhum papel de hoje só lê, e o 403 da `silver` sai do piso da classe. O
    `test_contracts.py` confere que cada escrita declara *uma* permissão; é este teste que
    prova que é a de escrita. Dona do veículo, `ana` só pode levar o 403 — nunca o 404."""
    _, headers = login(name="ana")
    joao = make_user(name="joao")
    vehicle_id = _cadastrar(client, headers)
    url = f"{VEHICLE_URL}{vehicle_id}"
    adicao = client.post(f"{url}/drivers", json={"user_name": "joao"}, headers=headers)
    assert adicao.status_code == 201
    monkeypatch.setitem(ROLE_PERMISSIONS, Role.USUARIO, frozenset({Permission.VEICULO_LER}))

    respostas = [
        client.post(VEHICLE_URL, json={"vin": OUTRO_VIN}, headers=headers),
        client.patch(url, json={"nickname": "x"}, headers=headers),
        client.delete(url, headers=headers),
        client.post(f"{url}/drivers", json={"user_name": "maria"}, headers=headers),
        client.delete(f"{url}/drivers/{joao['id']}", headers=headers),
        client.delete(f"{VEHICLE_URL}driving/{vehicle_id}", headers=headers),
    ]

    assert [r.status_code for r in respostas] == [403] * 6
    assert client.get(url, headers=headers).status_code == 200


# --- 404: o registro ----------------------------------------------------------


def test_o_escopo_de_leitura_nao_abre_a_escrita(client, login, monkeypatch):
    """Nenhum papel de hoje vê tudo e altera só o próprio, mas as permissões existem
    separadas para isso. Com o `monkeypatch`, a rota de escrita que aceitasse o escopo
    de leitura por engano reprova aqui: o admin, que tem os dois, não a pegaria."""
    _, headers_ana = login(name="ana")
    _, headers_bruno = login(name="bruno")
    vehicle_id = _cadastrar(client, headers_ana)
    url = f"{VEHICLE_URL}{vehicle_id}"
    monkeypatch.setitem(
        ROLE_PERMISSIONS,
        Role.USUARIO,
        ROLE_PERMISSIONS[Role.USUARIO] | {Permission.VEICULO_LER_DE_TODOS},
    )

    assert client.get(url, headers=headers_bruno).status_code == 200
    assert client.get(f"{url}/drivers", headers=headers_bruno).status_code == 200
    assert client.patch(url, json={"nickname": "x"}, headers=headers_bruno).status_code == 404
    assert client.delete(url, headers=headers_bruno).status_code == 404
    assert (
        client.post(f"{url}/drivers", json={"user_name": "ana"}, headers=headers_bruno).status_code
        == 404
    )
    assert client.delete(f"{url}/drivers/1", headers=headers_bruno).status_code == 404


def test_usuario_nao_ve_nem_altera_o_veiculo_de_outro_dono(client, login):
    """O dono de outro registro *pode* escrever veículo (passa no decorator), mas o
    escopo dele é o próprio: o alheio responde 404, porque um 403 confirmaria que ele
    existe."""
    _, headers_ana = login(name="ana")
    _, headers_bruno = login(name="bruno")
    vehicle_id = _cadastrar(client, headers_ana)
    url = f"{VEHICLE_URL}{vehicle_id}"

    assert client.get(url, headers=headers_bruno).status_code == 404
    assert client.patch(url, json={"nickname": "x"}, headers=headers_bruno).status_code == 404
    assert client.delete(url, headers=headers_bruno).status_code == 404


def test_administrador_altera_o_veiculo_de_outro_dono(client, login):
    _, headers_ana = login(name="ana")
    _, headers_admin = login(name="raiz", role=Role.ADMIN)
    vehicle_id = _cadastrar(client, headers_ana)

    response = client.patch(
        f"{VEHICLE_URL}{vehicle_id}", json={"nickname": "mexido"}, headers=headers_admin
    )

    assert response.status_code == 200
    assert response.get_json()["nickname"] == "mexido"


def test_administrador_cria_com_dono_concreto(client, login):
    """Nem administrador cria "sem dono": o POST usa `current_user.id`."""
    user, headers_admin = login(name="raiz", role=Role.ADMIN)
    vehicle_id = _cadastrar(client, headers_admin)

    body = client.get(f"{VEHICLE_URL}{vehicle_id}", headers=headers_admin).get_json()

    assert body["owner_id"] == user["id"]


# --- O papel mora no banco ----------------------------------------------------


def test_rebaixar_o_papel_vale_na_requisicao_seguinte(client, login, set_role):
    """Com o **mesmo** token. Se o papel voltasse a ser assinado no token, o
    rebaixamento só teria efeito quando ele expirasse."""
    _, headers_ana = login(name="ana")
    admin, headers_admin = login(name="raiz", role=Role.ADMIN)
    vehicle_id = _cadastrar(client, headers_ana)

    assert client.get(f"{VEHICLE_URL}{vehicle_id}", headers=headers_admin).status_code == 200

    set_role(admin["name"], Role.USUARIO)

    assert client.get(f"{VEHICLE_URL}{vehicle_id}", headers=headers_admin).status_code == 404


def test_promover_o_papel_vale_na_requisicao_seguinte(client, login, set_role):
    _, headers_ana = login(name="ana")
    bruno, headers_bruno = login(name="bruno")
    vehicle_id = _cadastrar(client, headers_ana)

    assert client.get(f"{VEHICLE_URL}{vehicle_id}", headers=headers_bruno).status_code == 404

    set_role(bruno["name"], Role.ADMIN)

    assert client.get(f"{VEHICLE_URL}{vehicle_id}", headers=headers_bruno).status_code == 200


# --- Autenticação continua antes de tudo --------------------------------------


def test_sem_token_responde_401_e_nao_403(client):
    """A ordem importa: 403 em rota sem token revelaria que ela existe e exige
    permissão. Quem não se identificou leva 401."""
    assert client.get(VEHICLE_URL).status_code == 401
    assert client.post(VEHICLE_URL, json={"vin": VIN}).status_code == 401
    assert client.patch(f"{VEHICLE_URL}1", json={"nickname": "x"}).status_code == 401


# --- Custo --------------------------------------------------------------------


def test_duas_verificacoes_na_mesma_requisicao_custam_uma_consulta(client, login, sql_log):
    """O POST passa por dois `permission_required` (o piso da classe e o do
    método). Se cada um verificasse o token por conta própria, o usuário seria
    carregado duas vezes — e "um SELECT por requisição autenticada" deixaria de
    ser verdade (ver `documentacao/02-decisoes.md`)."""
    _, headers = login(name="ana")
    sql_log.clear()

    assert client.post(VEHICLE_URL, json={"vin": VIN}, headers=headers).status_code == 201

    consultas_de_usuario = [s for s in sql_log if "FROM users" in s]
    assert len(consultas_de_usuario) == 1, consultas_de_usuario
