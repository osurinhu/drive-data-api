"""Os usuários do bronze, contra o Mongo de verdade: cada processo só faz o que usa.

Os usuários moram no banco de testes, então a suíte não encosta nos do desenvolvimento.
"""

import os
from urllib.parse import urlsplit

import pytest
from pymongo import ASCENDING, MongoClient
from pymongo.errors import OperationFailure

from repository.mongo import users
from repository.mongo.bronze_document import RECORDS, REJECTIONS

SEM_PERMISSAO = 13
CREDENCIAL_RECUSADA = 18

SENHA_WRITER = "senha-do-writer-nos-testes"
SENHA_READER = "senha-do-reader-nos-testes"


@pytest.fixture(autouse=True)
def _usuarios(mongo_db):
    assert mongo_db.name.endswith("_test"), mongo_db.name
    users.apply(mongo_db, writer_password=SENHA_WRITER, reader_password=SENHA_READER)
    yield
    mongo_db.command("dropAllUsersFromDatabase")


@pytest.fixture
def banco_de(mongo_db):
    """O banco de testes, visto pelo usuário e pela senha informados."""
    abertos = []

    def _abrir(usuario: str, senha: str):
        endereco = urlsplit(os.environ["FLASK_MONGO_URI"]).netloc.rpartition("@")[2]
        cliente = MongoClient(
            f"mongodb://{usuario}:{senha}@{endereco}/?authSource={mongo_db.name}",
            serverSelectionTimeoutMS=3000,
        )
        abertos.append(cliente)
        return cliente[mongo_db.name]

    yield _abrir
    for cliente in abertos:
        cliente.close()


@pytest.fixture
def do_writer(banco_de):
    return banco_de(users.WRITER, SENHA_WRITER)


@pytest.fixture
def do_reader(banco_de):
    return banco_de(users.READER, SENHA_READER)


def _recusado(acao) -> None:
    with pytest.raises(OperationFailure) as erro:
        acao()
    assert erro.value.code == SEM_PERMISSAO


def test_o_writer_grava_e_le(do_writer):
    do_writer[RECORDS].insert_one({"_id": "1"})
    do_writer[REJECTIONS].insert_one({"_id": "m:1"})

    assert do_writer[RECORDS].find_one({"_id": "1"}) == {"_id": "1"}


def test_o_reader_le_o_que_o_writer_gravou(do_writer, do_reader):
    do_writer[RECORDS].insert_one({"_id": "1"})

    assert do_reader[RECORDS].find_one({"_id": "1"}) == {"_id": "1"}


@pytest.mark.parametrize(
    "agir",
    [
        pytest.param(lambda c: c.insert_one({"_id": "novo"}), id="gravar"),
        pytest.param(lambda c: c.update_one({"_id": "1"}, {"$set": {"x": 1}}), id="alterar"),
        pytest.param(lambda c: c.delete_one({"_id": "1"}), id="apagar"),
        pytest.param(lambda c: c.create_index([("x", ASCENDING)]), id="criar índice"),
        pytest.param(lambda c: c.drop(), id="apagar a coleção"),
    ],
)
def test_o_reader_nao_muda_nada(do_writer, do_reader, agir):
    do_writer[RECORDS].insert_one({"_id": "1"})

    _recusado(lambda: agir(do_reader[RECORDS]))

    assert do_writer[RECORDS].count_documents({}) == 1


@pytest.mark.parametrize("usuario", [users.WRITER, users.READER])
def test_o_usuario_do_processo_nao_administra(banco_de, usuario):
    senha = SENHA_WRITER if usuario == users.WRITER else SENHA_READER
    banco = banco_de(usuario, senha)

    _recusado(lambda: banco.command("createUser", "intruso", pwd="x", roles=[]))


@pytest.mark.parametrize("usuario", [users.WRITER, users.READER])
def test_o_usuario_do_processo_nao_alcanca_outro_banco(banco_de, usuario):
    senha = SENHA_WRITER if usuario == users.WRITER else SENHA_READER
    outro = banco_de(usuario, senha).client["outro_banco_test"]

    _recusado(lambda: outro["qualquer"].find_one())


def test_aplicar_de_novo_troca_a_senha_e_mantem_o_papel(mongo_db, banco_de):
    """É assim que se troca a senha de um processo: muda no .env e o setup roda."""
    users.apply(mongo_db, writer_password="senha-nova-do-writer", reader_password=SENHA_READER)

    banco_de(users.WRITER, "senha-nova-do-writer")[RECORDS].insert_one({"_id": "1"})
    with pytest.raises(OperationFailure) as erro:
        banco_de(users.WRITER, SENHA_WRITER)[RECORDS].find_one()

    assert erro.value.code == CREDENCIAL_RECUSADA
    [usuario] = mongo_db.command("usersInfo", users.WRITER)["users"]
    assert usuario["roles"] == [{"role": "readWrite", "db": mongo_db.name}]
