"""Entidade de exemplo: CRUD, paginação, ownership e exclusão lógica.

Estes testes também exercitam a infraestrutura compartilhada (BaseService,
BaseRepository, handler de erros), então servem de molde para as entidades que
você acrescentar.
"""

ITEM_URL = "/api/v1/item/"


def _criar(client, headers, name: str, description: str | None = None):
    payload = {"name": name}
    if description is not None:
        payload["description"] = description
    return client.post(ITEM_URL, json=payload, headers=headers)


# --- Criação ------------------------------------------------------------------


def test_criar_item(client, login):
    user, headers = login()

    response = _criar(client, headers, "Primeiro item", "uma descrição")

    assert response.status_code == 201
    body = response.get_json()
    assert body["name"] == "Primeiro item"
    assert body["description"] == "uma descrição"
    assert body["owner_id"] == user["id"]
    assert body["id"] > 0


def test_criar_item_sem_nome_retorna_422(client, headers):
    response = client.post(ITEM_URL, json={}, headers=headers)

    assert response.status_code == 422


def test_nome_e_normalizado_antes_de_salvar(client, headers):
    """O gancho before_create do ItemService remove espaços nas pontas."""
    response = _criar(client, headers, "   com espaços   ")

    assert response.status_code == 201
    assert response.get_json()["name"] == "com espaços"


def test_nome_duplicado_para_o_mesmo_dono_retorna_409(client, headers):
    _criar(client, headers, "repetido")

    response = _criar(client, headers, "repetido")

    assert response.status_code == 409
    assert "nome" in response.get_json()["message"].lower()


def test_donos_diferentes_podem_usar_o_mesmo_nome(client, login):
    _, headers_a = login(name="ana")
    _, headers_b = login(name="bruno")

    assert _criar(client, headers_a, "comum").status_code == 201
    assert _criar(client, headers_b, "comum").status_code == 201


# --- Leitura ------------------------------------------------------------------


def test_buscar_item_por_id(client, headers):
    criado = _criar(client, headers, "buscavel").get_json()

    response = client.get(f"{ITEM_URL}{criado['id']}", headers=headers)

    assert response.status_code == 200
    assert response.get_json()["name"] == "buscavel"


def test_buscar_item_inexistente_retorna_404(client, headers):
    response = client.get(f"{ITEM_URL}999999", headers=headers)

    assert response.status_code == 404


def test_listagem_usa_o_envelope_de_paginacao(client, headers):
    for i in range(3):
        _criar(client, headers, f"item {i}")

    response = client.get(f"{ITEM_URL}?page=1&per_page=2", headers=headers)

    assert response.status_code == 200
    body = response.get_json()
    assert len(body["items"]) == 2
    assert body["current_page"] == 1
    assert body["total_of_items"] == 3
    assert body["total_of_pages"] == 2
    assert body["has_prev"] is False
    assert body["has_next"] is True


def test_segunda_pagina(client, headers):
    for i in range(3):
        _criar(client, headers, f"item {i}")

    body = client.get(f"{ITEM_URL}?page=2&per_page=2", headers=headers).get_json()

    assert len(body["items"]) == 1
    assert body["has_prev"] is True
    assert body["has_next"] is False


def test_filtro_por_nome_e_parcial_e_ignora_caixa(client, headers):
    _criar(client, headers, "Relatório mensal")
    _criar(client, headers, "Outra coisa")

    body = client.get(f"{ITEM_URL}?name=relatorio", headers=headers).get_json()
    assert body["total_of_items"] == 0

    body = client.get(f"{ITEM_URL}?name=RELATÓRIO", headers=headers).get_json()
    assert body["total_of_items"] == 1
    assert body["items"][0]["name"] == "Relatório mensal"


def test_filtro_vazio_nao_restringe_a_listagem(client, headers):
    """Regressão: nos projetos de origem um filtro vazio ainda entrava na
    consulta como `LIKE '%%'` por causa de uma condição sempre verdadeira."""
    _criar(client, headers, "visivel")

    body = client.get(f"{ITEM_URL}?name=", headers=headers).get_json()

    assert body["total_of_items"] == 1


def test_per_page_acima_do_maximo_retorna_422(client, headers):
    response = client.get(f"{ITEM_URL}?per_page=1000", headers=headers)

    assert response.status_code == 422


# --- Atualização --------------------------------------------------------------


def test_atualizacao_parcial_mantem_os_outros_campos(client, headers):
    criado = _criar(client, headers, "antes", "descrição original").get_json()

    response = client.patch(f"{ITEM_URL}{criado['id']}", json={"name": "depois"}, headers=headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body["name"] == "depois"
    assert body["description"] == "descrição original"


def test_atualizacao_aceita_limpar_a_descricao(client, headers):
    """Regressão: os projetos de origem descartavam valores falsy no update,
    então não havia como gravar null, "" ou 0."""
    criado = _criar(client, headers, "item", "tinha descrição").get_json()

    response = client.patch(
        f"{ITEM_URL}{criado['id']}", json={"description": None}, headers=headers
    )

    assert response.status_code == 200
    assert response.get_json()["description"] is None


def test_atualizacao_sem_nenhum_campo_retorna_422(client, headers):
    criado = _criar(client, headers, "item").get_json()

    response = client.patch(f"{ITEM_URL}{criado['id']}", json={}, headers=headers)

    assert response.status_code == 422


# --- Exclusão lógica ----------------------------------------------------------


def test_exclusao_remove_o_item_das_consultas(client, headers):
    criado = _criar(client, headers, "temporario").get_json()

    assert client.delete(f"{ITEM_URL}{criado['id']}", headers=headers).status_code == 200
    assert client.get(f"{ITEM_URL}{criado['id']}", headers=headers).status_code == 404
    assert client.get(ITEM_URL, headers=headers).get_json()["total_of_items"] == 0


def test_nome_pode_ser_reutilizado_apos_a_exclusao(client, headers):
    """Valida o índice único parcial (`WHERE deleted = false`): sem ele, o
    registro excluído logicamente bloquearia o nome para sempre."""
    criado = _criar(client, headers, "reciclavel").get_json()
    client.delete(f"{ITEM_URL}{criado['id']}", headers=headers)

    response = _criar(client, headers, "reciclavel")

    assert response.status_code == 201


# --- Ownership ----------------------------------------------------------------


def test_item_de_outro_usuario_responde_404(client, login):
    _, headers_a = login(name="ana")
    _, headers_b = login(name="bruno")
    criado = _criar(client, headers_a, "privado").get_json()

    # 404 e não 403: um 403 confirmaria que o item existe.
    assert client.get(f"{ITEM_URL}{criado['id']}", headers=headers_b).status_code == 404
    assert (
        client.patch(
            f"{ITEM_URL}{criado['id']}", json={"name": "invadido"}, headers=headers_b
        ).status_code
        == 404
    )
    assert client.delete(f"{ITEM_URL}{criado['id']}", headers=headers_b).status_code == 404


def test_listagem_mostra_somente_os_itens_do_dono(client, login):
    _, headers_a = login(name="ana")
    _, headers_b = login(name="bruno")
    _criar(client, headers_a, "da ana")
    _criar(client, headers_b, "do bruno")

    body = client.get(ITEM_URL, headers=headers_a).get_json()

    assert body["total_of_items"] == 1
    assert body["items"][0]["name"] == "da ana"
