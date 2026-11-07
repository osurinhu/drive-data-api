"""Autorização por permissões.

O que estes testes fixam, e que nenhum teste de CRUD pegaria:

* a permissão decide a **operação** (403) e o ownership decide o **registro**
  (404) — duas respostas diferentes, de propósito;
* cada escrita declara a sua permissão: o piso da classe é de leitura. O
  `test_contracts.py` reprova a escrita sem decorator; o 403 daqui prova que
  a permissão declarada é a de escrita;
* escopo de leitura e escopo de escrita são permissões separadas, então existe
  um papel que vê tudo e altera só o que é dele;
* o papel é lido do banco, não do token.
"""

from domain.authz import ROLE_PERMISSIONS, Permission, Role, has_permission, permissions_of

ITEM_URL = "/api/v1/item/"


def _criar(client, headers, name: str) -> int:
    response = client.post(ITEM_URL, json={"name": name}, headers=headers)
    assert response.status_code == 201, response.get_json()
    return response.get_json()["id"]


# --- O mapa ------------------------------------------------------------------


def test_os_papeis_sao_cumulativos():
    """A hierarquia existe por inclusão de conjuntos, não por peso numérico."""
    assert permissions_of(Role.LEITOR) < permissions_of(Role.USUARIO)
    assert permissions_of(Role.USUARIO) < permissions_of(Role.GERENTE)
    assert permissions_of(Role.GERENTE) < permissions_of(Role.ADMIN)


def test_a_silver_e_um_ramo_a_parte():
    """A conta da silver só lê o bronze: não tem permissão nenhuma da hierarquia,
    e nenhum papel dela lê o bronze, nem o admin (que contém todos os outros)."""
    assert permissions_of(Role.SILVER)
    assert permissions_of(Role.SILVER).isdisjoint(permissions_of(Role.ADMIN))


def test_has_permission_exige_todas_as_informadas():
    assert has_permission(Role.ADMIN, Permission.ITEM_LER, Permission.ITEM_ESCREVER_DE_TODOS)
    assert not has_permission(Role.GERENTE, Permission.ITEM_LER, Permission.ITEM_ESCREVER_DE_TODOS)


def test_papel_desconhecido_nao_tem_permissao_nenhuma():
    """Um papel que chegou do banco sem entrada no mapa precisa negar acesso —
    não virar KeyError, que o handler genérico transformaria em 500."""
    assert permissions_of("papel-que-nao-existe") == frozenset()  # type: ignore[arg-type]


# --- 403: a operação ----------------------------------------------------------


def test_sem_a_permissao_da_operacao_responde_403(client, login):
    """`leitor` tem `item:ler` e não `item:escrever`: é o papel que a base traz
    para que este caso exista sem precisar remendar o mapa de permissões."""
    _, headers = login(name="le", role=Role.LEITOR)

    response = client.post(ITEM_URL, json={"name": "proibido"}, headers=headers)

    assert response.status_code == 403
    assert response.get_json()["message"] == "Seu perfil não permite esta operação."


def test_o_403_vem_antes_da_validacao_do_corpo(client, login):
    """Quem não pode fazer a operação não deve descobrir o formato esperado do
    corpo dela: 403, e não o 422 que o schema daria."""
    _, headers = login(name="le", role=Role.LEITOR)

    response = client.post(ITEM_URL, json={"campo": "invalido"}, headers=headers)

    assert response.status_code == 403


def test_leitor_nao_altera_nem_exclui_o_proprio_item(client, login, set_role):
    """O piso da classe é de leitura: cada escrita depende do seu próprio
    `@permission_required`, e só este teste diz se ele exige a permissão certa
    (o `test_contracts.py` confere que existe). Dona do item, `ana` só pode levar
    o 403 da permissão — nunca o 404 do ownership."""
    ana, headers = login(name="ana")
    item_id = _criar(client, headers, "da ana")
    set_role(ana["name"], Role.LEITOR)

    patch = client.patch(f"{ITEM_URL}{item_id}", json={"name": "mexido"}, headers=headers)
    delete = client.delete(f"{ITEM_URL}{item_id}", headers=headers)

    assert patch.status_code == 403
    assert delete.status_code == 403
    assert client.get(f"{ITEM_URL}{item_id}", headers=headers).get_json()["name"] == "da ana"


def test_sem_permissao_alguma_nenhuma_rota_de_item_abre(client, login, monkeypatch):
    """O único `monkeypatch` que sobrou, e o porquê: nenhum papel da base fica
    sem `item:ler`, então é o único jeito de provar que o piso declarado na
    classe nega — o GET não tem decorator próprio."""
    _, headers = login(name="le", role=Role.LEITOR)
    monkeypatch.setitem(ROLE_PERMISSIONS, Role.LEITOR, frozenset())

    assert client.get(ITEM_URL, headers=headers).status_code == 403
    assert client.get(f"{ITEM_URL}1", headers=headers).status_code == 403


# --- 404: o registro ----------------------------------------------------------


def test_gerente_enxerga_o_item_de_outro_dono(client, login):
    _, headers_ana = login(name="ana")
    _, headers_gerente = login(name="gi", role=Role.GERENTE)
    item_id = _criar(client, headers_ana, "da ana")

    assert client.get(f"{ITEM_URL}{item_id}", headers=headers_gerente).status_code == 200
    assert client.get(ITEM_URL, headers=headers_gerente).get_json()["total_of_items"] == 1


def test_gerente_nao_altera_o_item_de_outro_dono(client, login):
    """O ponto do modelo: o gerente *pode* escrever item (passa no decorator),
    mas o escopo de escrita dele é o próprio — então o item alheio responde 404,
    pelo mesmo motivo de sempre (um 403 confirmaria que ele existe)."""
    _, headers_ana = login(name="ana")
    _, headers_gerente = login(name="gi", role=Role.GERENTE)
    item_id = _criar(client, headers_ana, "da ana")

    assert (
        client.patch(
            f"{ITEM_URL}{item_id}", json={"name": "mexido"}, headers=headers_gerente
        ).status_code
        == 404
    )
    assert client.delete(f"{ITEM_URL}{item_id}", headers=headers_gerente).status_code == 404


def test_administrador_altera_o_item_de_outro_dono(client, login):
    _, headers_ana = login(name="ana")
    _, headers_admin = login(name="raiz", role=Role.ADMIN)
    item_id = _criar(client, headers_ana, "da ana")

    response = client.patch(f"{ITEM_URL}{item_id}", json={"name": "mexido"}, headers=headers_admin)

    assert response.status_code == 200
    assert response.get_json()["name"] == "mexido"


def test_administrador_cria_com_dono_concreto(client, login):
    """Nem administrador cria "sem dono": o POST usa `current_user.id`."""
    user, headers_admin = login(name="raiz", role=Role.ADMIN)
    item_id = _criar(client, headers_admin, "do admin")

    body = client.get(f"{ITEM_URL}{item_id}", headers=headers_admin).get_json()

    assert body["owner_id"] == user["id"]


# --- O papel mora no banco ----------------------------------------------------


def test_rebaixar_o_papel_vale_na_requisicao_seguinte(client, login, set_role):
    """Com o **mesmo** token. Se o papel voltasse a ser assinado no token, o
    rebaixamento só teria efeito quando ele expirasse."""
    _, headers_ana = login(name="ana")
    admin, headers_admin = login(name="raiz", role=Role.ADMIN)
    item_id = _criar(client, headers_ana, "da ana")

    assert client.get(f"{ITEM_URL}{item_id}", headers=headers_admin).status_code == 200

    set_role(admin["name"], Role.USUARIO)

    assert client.get(f"{ITEM_URL}{item_id}", headers=headers_admin).status_code == 404


def test_promover_o_papel_vale_na_requisicao_seguinte(client, login, set_role):
    _, headers_ana = login(name="ana")
    gi, headers_gi = login(name="gi")
    item_id = _criar(client, headers_ana, "da ana")

    assert client.get(f"{ITEM_URL}{item_id}", headers=headers_gi).status_code == 404

    set_role(gi["name"], Role.GERENTE)

    assert client.get(f"{ITEM_URL}{item_id}", headers=headers_gi).status_code == 200


# --- Autenticação continua antes de tudo --------------------------------------


def test_sem_token_responde_401_e_nao_403(client):
    """A ordem importa: 403 em rota sem token revelaria que ela existe e exige
    permissão. Quem não se identificou leva 401."""
    assert client.get(ITEM_URL).status_code == 401
    assert client.post(ITEM_URL, json={"name": "x"}).status_code == 401
    assert client.patch(f"{ITEM_URL}1", json={"name": "x"}).status_code == 401


# --- Custo --------------------------------------------------------------------


def test_duas_verificacoes_na_mesma_requisicao_custam_uma_consulta(client, login, sql_log):
    """O POST passa por dois `permission_required` (o piso da classe e o do
    método). Se cada um verificasse o token por conta própria, o usuário seria
    carregado duas vezes — e "um SELECT por requisição autenticada" deixaria de
    ser verdade (ver `documentacao/02-decisoes.md`)."""
    _, headers = login(name="ana")
    sql_log.clear()

    assert client.post(ITEM_URL, json={"name": "medido"}, headers=headers).status_code == 201

    consultas_de_usuario = [s for s in sql_log if "FROM users" in s]
    assert len(consultas_de_usuario) == 1, consultas_de_usuario
