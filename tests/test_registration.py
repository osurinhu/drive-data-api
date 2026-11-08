"""Cadastro público: nasce sempre `usuario`, e os limites da senha."""

import pytest

from domain.authz import Role
from service.errors import BusinessRuleError

REGISTER_URL = "/api/v1/auth/register"
TOKEN_URL = "/api/v1/auth/token"
SENHA = "uma-senha-boa-123"


def test_o_cadastro_e_publico_e_cria_um_usuario_comum(client):
    response = client.post(REGISTER_URL, json={"name": "ana", "password": SENHA})

    assert response.status_code == 201, response.get_json()
    body = response.get_json()
    assert set(body) == {"id", "name", "role", "created_at", "permissions"}
    assert (body["name"], body["role"]) == ("ana", "usuario")


def test_quem_se_cadastrou_consegue_entrar(client):
    client.post(REGISTER_URL, json={"name": "ana", "password": SENHA})

    assert client.post(TOKEN_URL, json={"name": "ana", "password": SENHA}).status_code == 200


def test_o_cadastro_nao_aceita_escolher_o_papel(client):
    """Nenhum caminho HTTP escala privilégio: o campo nem existe no contrato."""
    response = client.post(
        REGISTER_URL, json={"name": "ana", "password": SENHA, "role": Role.ADMIN.value}
    )

    assert response.status_code == 422
    assert client.post(TOKEN_URL, json={"name": "ana", "password": SENHA}).status_code == 401


def test_nome_repetido_responde_409(client, make_user):
    make_user(name="ana")

    response = client.post(REGISTER_URL, json={"name": "ana", "password": SENHA})

    assert response.status_code == 409
    assert response.get_json()["message"] == "Já existe um usuário com esse nome."


def test_o_nome_e_aparado_nas_pontas(client):
    response = client.post(REGISTER_URL, json={"name": "  ana ", "password": SENHA})

    assert response.get_json()["name"] == "ana"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"password": SENHA}, id="sem-nome"),
        pytest.param({"name": "ana"}, id="sem-senha"),
        pytest.param({"name": "", "password": SENHA}, id="nome-vazio"),
        pytest.param({"name": "   ", "password": SENHA}, id="nome-so-espacos"),
        pytest.param({"name": "a" * 51, "password": SENHA}, id="nome-longo"),
        pytest.param({"name": "ana", "password": "a" * 7}, id="senha-curta"),
        pytest.param({"name": "ana", "password": "a" * 129}, id="senha-longa"),
    ],
)
def test_entrada_invalida_retorna_422(client, payload):
    assert client.post(REGISTER_URL, json=payload).status_code == 422


@pytest.mark.parametrize("tamanho", [8, 128])
def test_a_senha_aceita_os_limites(client, tamanho):
    response = client.post(REGISTER_URL, json={"name": "ana", "password": "a" * tamanho})

    assert response.status_code == 201


@pytest.mark.parametrize("tamanho", [7, 129])
def test_o_servico_confere_a_senha_de_quem_nao_passa_por_schema(app, tamanho):
    with app.app_context(), pytest.raises(BusinessRuleError):
        app.container.user_service().register(name="ana", password="a" * tamanho)


def test_create_user_com_senha_curta_falha_sem_traceback(app):
    resultado = app.test_cli_runner().invoke(
        args=["create-user", "--name", "ana", "--password", "curta"]
    )

    assert resultado.exit_code == 1
    assert "A senha deve ter" in resultado.output
