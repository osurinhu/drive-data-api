"""Tokens de renovação: o par do login, a rotação, o reuso, o logout e os comandos.

As revogações acontecem no banco, então estes testes passam sempre pela requisição
real: o que importa é o que o próximo uso do token responde.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from flask_jwt_extended import create_access_token, create_refresh_token

from domain.authz import ANY_OWNER
from domain.models import RefreshToken

TOKEN_URL = "/api/v1/auth/token"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
ME_URL = "/api/v1/auth/me"


def _par(client, user: dict) -> dict:
    response = client.post(TOKEN_URL, json={"name": user["name"], "password": user["password"]})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _renovar(client, refresh_token: str):
    return client.post(REFRESH_URL, headers=_bearer(refresh_token))


def _sair(client, refresh_token: str):
    return client.post(LOGOUT_URL, headers=_bearer(refresh_token))


def _claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


# --- O par ----------------------------------------------------------------------


def test_o_login_devolve_o_par_de_tokens(client, make_user):
    body = _par(client, make_user(name="ana"))

    assert set(body) == {
        "access_token",
        "refresh_token",
        "token_type",
        "expires_in",
        "refresh_expires_in",
    }
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 900
    assert body["refresh_expires_in"] == 2592000


def test_as_claims_dos_dois_tokens(client, make_user):
    user = make_user(name="ana")

    body = _par(client, user)

    acesso, renovacao = _claims(body["access_token"]), _claims(body["refresh_token"])
    assert (acesso["sub"], acesso["type"], acesso["iss"]) == (
        str(user["id"]),
        "access",
        "drive-data",
    )
    assert acesso["fresh"] is True
    assert acesso["exp"] - acesso["iat"] == 900
    assert (renovacao["sub"], renovacao["type"], renovacao["iss"]) == (
        str(user["id"]),
        "refresh",
        "drive-data",
    )
    assert renovacao["exp"] - renovacao["iat"] == 2592000


def test_token_sem_o_emissor_certo_e_recusado(app, client, make_user):
    """Assinado com a chave certa, mas sem o `iss` desta aplicação."""
    user = make_user(name="ana")
    agora = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": str(user["id"]),
            "type": "access",
            "fresh": False,
            "jti": "sem-emissor",
            "iat": agora,
            "nbf": agora,
            "exp": agora + timedelta(minutes=5),
        },
        app.config["JWT_SECRET_KEY"],
        algorithm="HS256",
    )

    assert client.get(ME_URL, headers=_bearer(token)).status_code == 401


def test_o_token_de_renovacao_nao_abre_as_outras_rotas(client, make_user):
    body = _par(client, make_user(name="ana"))

    assert client.get(ME_URL, headers=_bearer(body["refresh_token"])).status_code == 401


def test_o_token_de_acesso_nao_renova_nem_encerra(client, make_user):
    body = _par(client, make_user(name="ana"))

    assert _renovar(client, body["access_token"]).status_code == 401
    assert _sair(client, body["access_token"]).status_code == 401


def test_renovar_e_sair_exigem_token(client):
    assert client.post(REFRESH_URL).status_code == 401
    assert client.post(LOGOUT_URL).status_code == 401


def test_o_token_de_acesso_nao_consulta_a_tabela_de_renovacao(client, make_user, sql_log):
    """A revogação custa banco só para os tokens de renovação: a requisição comum
    continua com um SELECT, o do usuário."""
    body = _par(client, make_user(name="ana"))
    sql_log.clear()

    assert client.get(ME_URL, headers=_bearer(body["access_token"])).status_code == 200

    assert len(sql_log) == 1
    assert "FROM users" in sql_log[0]


# --- Rotação --------------------------------------------------------------------


def test_renovar_devolve_um_par_novo_que_funciona(client, make_user):
    primeiro = _par(client, make_user(name="ana"))

    response = _renovar(client, primeiro["refresh_token"])

    assert response.status_code == 200
    segundo = response.get_json()
    assert segundo["refresh_token"] != primeiro["refresh_token"]
    assert _claims(segundo["access_token"])["fresh"] is False
    assert client.get(ME_URL, headers=_bearer(segundo["access_token"])).status_code == 200
    assert _renovar(client, segundo["refresh_token"]).status_code == 200


def test_o_token_trocado_aponta_o_sucessor_e_mantem_a_familia(app, client, make_user):
    primeiro = _par(client, make_user(name="ana"))
    segundo = _renovar(client, primeiro["refresh_token"]).get_json()
    jti_antigo = _claims(primeiro["refresh_token"])["jti"]
    jti_novo = _claims(segundo["refresh_token"])["jti"]

    with app.app_context():
        repositorio = app.container.refresh_token_repository()
        antigo, novo = repositorio.get_active(jti_antigo), repositorio.get_active(jti_novo)

        assert antigo.replaced_by_jti == jti_novo
        assert novo.family_id == antigo.family_id == jti_antigo


def test_reapresentar_um_token_ja_trocado_derruba_a_familia(client, make_user):
    """Quem reapresenta pode ser o dono ou quem copiou o token: os dois perdem a sessão."""
    primeiro = _par(client, make_user(name="ana"))
    segundo = _renovar(client, primeiro["refresh_token"]).get_json()

    reuso = _renovar(client, primeiro["refresh_token"])

    assert reuso.status_code == 401
    assert reuso.get_json()["message"] == "Token de renovação inválido."
    assert _renovar(client, segundo["refresh_token"]).status_code == 401
    assert _renovar(client, primeiro["refresh_token"]).status_code == 401


def test_o_reuso_nao_derruba_as_outras_sessoes_do_usuario(client, make_user):
    user = make_user(name="ana")
    celular = _par(client, user)
    tablet = _par(client, user)
    _renovar(client, celular["refresh_token"])

    assert _renovar(client, celular["refresh_token"]).status_code == 401

    assert _renovar(client, tablet["refresh_token"]).status_code == 200


def test_a_renovacao_simultanea_espera_e_ve_o_token_ja_trocado(app, make_user):
    """A primeira renovação está no meio da transação (a linha travada, o sucessor
    apontado). A segunda precisa esperar e, depois do commit, ver um reuso: sem a trava,
    os dois lados sairiam com um sucessor."""
    par = _par(app.test_client(), make_user(name="ana"))
    jti = _claims(par["refresh_token"])["jti"]

    with app.app_context(), ThreadPoolExecutor(max_workers=1) as pool:
        repositorio = app.container.refresh_token_repository()
        repositorio.update(repositorio.get_active_for_update(jti), replaced_by_jti="sucessor")
        segunda = pool.submit(_renovar, app.test_client(), par["refresh_token"])
        with pytest.raises(TimeoutError):
            segunda.result(timeout=1)

        repositorio.commit()

        assert segunda.result(timeout=10).status_code == 401
        assert repositorio.get_active(jti) is None


def test_a_revogacao_alcanca_o_token_que_uma_renovacao_em_andamento_criou(app, make_user):
    """A renovação inseriu o sucessor e ainda não fez o commit quando o logout chega. O
    logout espera, e a revogação precisa pegar o sucessor: é para isso que ela trava as
    linhas numa instrução e faz o ``UPDATE`` em outra (``RefreshTokenRepository._revoke``)."""
    par = _par(app.test_client(), make_user(name="ana"))
    jti = _claims(par["refresh_token"])["jti"]

    with app.app_context(), ThreadPoolExecutor(max_workers=1) as pool:
        repositorio = app.container.refresh_token_repository()
        em_andamento = repositorio.get_active_for_update(jti)
        repositorio.add(
            RefreshToken(
                jti="sucessor",
                family_id=em_andamento.family_id,
                user_id=em_andamento.user_id,
                expires_at=em_andamento.expires_at,
            )
        )
        logout = pool.submit(_sair, app.test_client(), par["refresh_token"])
        with pytest.raises(TimeoutError):
            logout.result(timeout=1)

        repositorio.commit()

        assert logout.result(timeout=10).status_code == 204
        assert repositorio.get_active("sucessor") is None


def test_token_de_renovacao_expirado_responde_401(app, client, make_user):
    user = make_user(name="ana")
    with app.test_request_context():
        token = create_refresh_token(
            identity=SimpleNamespace(id=user["id"]), expires_delta=timedelta(seconds=-1)
        )

    response = _renovar(client, token)

    assert response.status_code == 401
    assert response.get_json()["message"] == "Token expirado."


def test_token_bem_assinado_que_nunca_foi_registrado_e_recusado(app, client, make_user):
    user = make_user(name="ana")
    with app.test_request_context():
        token = create_refresh_token(identity=SimpleNamespace(id=user["id"]))

    assert _renovar(client, token).status_code == 401
    assert _sair(client, token).status_code == 401


def test_usuario_excluido_nao_renova(app, client, make_user):
    user = make_user(name="ana")
    par = _par(client, user)
    with app.app_context():
        app.container.user_service().delete(user["id"], ANY_OWNER)

    response = _renovar(client, par["refresh_token"])

    assert response.status_code == 401
    assert response.get_json()["message"] == "Usuário do token não existe mais."


# --- Logout ---------------------------------------------------------------------


def test_o_logout_encerra_a_sessao(client, make_user):
    par = _par(client, make_user(name="ana"))

    response = _sair(client, par["refresh_token"])

    assert response.status_code == 204
    assert response.data == b""
    assert _renovar(client, par["refresh_token"]).status_code == 401
    assert _sair(client, par["refresh_token"]).status_code == 401


def test_o_logout_com_um_token_antigo_derruba_o_sucessor(client, make_user):
    primeiro = _par(client, make_user(name="ana"))
    segundo = _renovar(client, primeiro["refresh_token"]).get_json()

    assert _sair(client, primeiro["refresh_token"]).status_code == 204

    assert _renovar(client, segundo["refresh_token"]).status_code == 401


def test_o_logout_nao_derruba_as_outras_sessoes(client, make_user):
    user = make_user(name="ana")
    celular = _par(client, user)
    tablet = _par(client, user)

    assert _sair(client, celular["refresh_token"]).status_code == 204

    assert _renovar(client, tablet["refresh_token"]).status_code == 200


def test_o_token_de_acesso_continua_valendo_depois_do_logout(client, make_user):
    """O custo aceito: revogar o de acesso custaria uma consulta em toda requisição, e
    ele expira em 15 minutos."""
    par = _par(client, make_user(name="ana"))

    _sair(client, par["refresh_token"])

    assert client.get(ME_URL, headers=_bearer(par["access_token"])).status_code == 200


def test_token_de_acesso_expirado_responde_401(app, client, make_user):
    user = make_user(name="ana")
    with app.test_request_context():
        token = create_access_token(
            identity=SimpleNamespace(id=user["id"]), expires_delta=timedelta(seconds=-1)
        )

    response = client.get(ME_URL, headers=_bearer(token))

    assert response.status_code == 401
    assert response.get_json()["message"] == "Token expirado."


# --- Comandos -------------------------------------------------------------------

NOVA_SENHA = "uma-senha-nova-123"


def test_set_password_troca_a_senha_e_encerra_todas_as_sessoes(app, client, make_user):
    user = make_user(name="ana")
    celular = _par(client, user)
    tablet = _par(client, user)

    resultado = app.test_cli_runner().invoke(
        args=["set-password", "--name", "ana", "--password", NOVA_SENHA]
    )

    assert resultado.exit_code == 0, resultado.output
    assert "trocada" in resultado.output
    assert _renovar(client, celular["refresh_token"]).status_code == 401
    assert _renovar(client, tablet["refresh_token"]).status_code == 401
    assert (
        client.post(TOKEN_URL, json={"name": "ana", "password": user["password"]}).status_code
        == 401
    )
    assert client.post(TOKEN_URL, json={"name": "ana", "password": NOVA_SENHA}).status_code == 200


def test_set_password_nao_mexe_nas_sessoes_dos_outros(app, client, make_user):
    make_user(name="ana")
    bruno = make_user(name="bruno")
    par = _par(client, bruno)

    app.test_cli_runner().invoke(args=["set-password", "--name", "ana", "--password", NOVA_SENHA])

    assert _renovar(client, par["refresh_token"]).status_code == 200


def test_set_password_de_usuario_inexistente_falha_sem_traceback(app):
    resultado = app.test_cli_runner().invoke(
        args=["set-password", "--name", "ninguem", "--password", NOVA_SENHA]
    )

    assert resultado.exit_code == 1
    assert "não encontrado" in resultado.output


def test_set_password_com_senha_curta_nao_derruba_as_sessoes(app, client, make_user):
    par = _par(client, make_user(name="ana"))

    resultado = app.test_cli_runner().invoke(
        args=["set-password", "--name", "ana", "--password", "curta"]
    )

    assert resultado.exit_code == 1
    assert "A senha deve ter" in resultado.output
    assert _renovar(client, par["refresh_token"]).status_code == 200


def test_prune_tokens_apaga_so_os_expirados(app, make_user):
    user = make_user(name="ana")
    agora = datetime.now(UTC)
    with app.app_context():
        servico = app.container.token_service()
        servico.register(user["id"], "expirado", agora - timedelta(seconds=1))
        servico.register(user["id"], "valido", agora + timedelta(hours=1))

    resultado = app.test_cli_runner().invoke(args=["prune-tokens"])

    assert resultado.exit_code == 0, resultado.output
    assert "1 token" in resultado.output
    with app.app_context():
        repositorio = app.container.refresh_token_repository()
        assert repositorio.get_active("expirado") is None
        assert repositorio.get_active("valido") is not None
