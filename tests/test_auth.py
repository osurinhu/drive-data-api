"""Autenticação: login e identidade. A renovação fica em `test_refresh.py`."""

import logging

import jwt

from domain.authz import ROLE_PERMISSIONS, Role


def test_token_com_credenciais_validas(client, make_user):
    user = make_user(name="ana")

    response = client.post(
        "/api/v1/auth/token", json={"name": user["name"], "password": user["password"]}
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["expires_in"] > 0


def test_json_aninhado_demais_responde_400_e_deixa_aviso_no_log(client, caplog):
    """O ``json.loads`` estoura a pilha: sem tratamento, a rota pública daria 500."""
    with caplog.at_level(logging.WARNING):
        response = client.post(
            "/api/v1/auth/token",
            data="[" * 200_000,
            content_type="application/json",
            headers={"X-Request-ID": "req-json-fundo"},
        )

    assert response.status_code == 400
    assert response.get_json()["request_id"] == "req-json-fundo"
    aviso = next(r for r in caplog.records if "aninhado demais" in r.getMessage())
    assert aviso.levelno == logging.WARNING
    assert "req-json-fundo" in aviso.getMessage()


def test_token_com_senha_errada_retorna_401(client, make_user):
    make_user(name="ana")

    response = client.post("/api/v1/auth/token", json={"name": "ana", "password": "errada"})

    assert response.status_code == 401
    assert response.get_json()["message"] == "Credenciais inválidas."


def test_token_de_usuario_inexistente_tem_a_mesma_mensagem(client):
    """A resposta não deve permitir descobrir quais nomes existem."""
    response = client.post(
        "/api/v1/auth/token", json={"name": "ninguem", "password": "qualquer-senha"}
    )

    assert response.status_code == 401
    assert response.get_json()["message"] == "Credenciais inválidas."


def test_token_sem_campos_obrigatorios_retorna_422(client):
    response = client.post("/api/v1/auth/token", json={})

    assert response.status_code == 422


def test_me_sem_token_retorna_401(client):
    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.get_json()["code"] == 401


def test_me_com_token_invalido_retorna_401(client):
    response = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nao-e-um-token"})

    assert response.status_code == 401


def test_me_devolve_o_usuario_autenticado_sem_a_senha(client, login):
    user, auth_headers = login(name="ana")

    response = client.get("/api/v1/auth/me", headers=auth_headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body["id"] == user["id"]
    assert body["name"] == "ana"
    assert body["role"] == "usuario"
    assert "password" not in body


def test_me_devolve_o_papel_e_as_permissoes_dele(client, login):
    """O cliente precisa das permissões para decidir o que mostrar. Sem isso,
    ele reimplementaria o mapa papel -> permissões por conta própria."""
    _, auth_headers = login(name="raiz", role=Role.ADMIN)

    response = client.get("/api/v1/auth/me", headers=auth_headers)

    assert response.status_code == 200
    body = response.get_json()
    assert body["role"] == "admin"
    assert set(body["permissions"]) == {p.value for p in ROLE_PERMISSIONS[Role.ADMIN]}


def test_o_token_nao_carrega_o_papel(login):
    """O papel é lido do banco a cada requisição. Se voltar a ser assinado no
    token, uma troca de papel só valeria quando o token expirasse."""
    _, auth_headers = login(name="raiz", role=Role.ADMIN)

    payload = jwt.decode(
        auth_headers["Authorization"].removeprefix("Bearer "),
        options={"verify_signature": False},
    )

    assert payload["sub"]
    assert "role" not in payload
