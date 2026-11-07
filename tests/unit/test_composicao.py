"""Composição dos processos: o que cada perfil do settings.toml carrega.

A mesma imagem roda vários processos, e cada um carrega só o que usa. "Não usa"
quer dizer que o processo não importa a extensão nem a rota, não exige a
configuração delas e não as mostra no health. O [default] é a união dos perfis,
para que a suíte, que roda nele, exercite todos os módulos.

Os testes leem só a configuração (``build_config``): montar a aplicação de outro
perfil dentro da suíte bagunçaria o ``api`` e o ``v1`` de ``ext.api``, que são
globais. Os que montam a aplicação o fazem num processo à parte.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import ext.config
from ext.config import PROCESS_ENVVAR, build_config

PERFIS = ("api", "bronze_api", "consumer", "setup")

#: A fronteira de cada processo: as extensões que ele nunca carrega.
FORA_DO_PERFIL = {
    "api": {"ext.mongo"},
    "bronze_api": {"ext.rabbitmq"},
    "consumer": {
        "ext.database",
        "ext.container",
        "ext.auth",
        "ext.cors",
        "ext.error_handler",
        "ext.commands",
        "ext.api",
    },
    "setup": {"ext.container", "ext.auth", "ext.cors", "ext.error_handler", "ext.api"},
}

#: A extensão que registra cada sonda do health.
EXTENSAO_DA_SONDA = {
    "database": "ext.database",
    "mongo": "ext.mongo",
    "rabbitmq": "ext.rabbitmq",
}


def _perfil(monkeypatch, nome):
    monkeypatch.setenv(PROCESS_ENVVAR, nome)
    return build_config()


def test_o_default_carrega_a_uniao_dos_perfis(monkeypatch):
    """Falhou? Um módulo entrou num perfil e não no [default] (a suíte não o
    exercita), ou está no [default] sem que processo nenhum o use."""
    perfis = [_perfil(monkeypatch, nome) for nome in PERFIS]
    default = _perfil(monkeypatch, "default")

    for chave in ("EXTENSIONS", "API_V1_ROUTES"):
        uniao = set().union(*(perfil[chave] for perfil in perfis))
        assert set(default[chave]) == uniao, chave


@pytest.mark.parametrize("nome", PERFIS)
def test_o_perfil_nao_carrega_o_que_o_processo_nao_usa(monkeypatch, nome):
    extensoes = set(_perfil(monkeypatch, nome)["EXTENSIONS"])

    assert not extensoes & FORA_DO_PERFIL[nome], sorted(extensoes & FORA_DO_PERFIL[nome])


@pytest.mark.parametrize("nome", ("default", *PERFIS))
def test_a_observabilidade_vem_primeiro_e_a_api_por_ultimo(monkeypatch, nome):
    """A ordem da lista é a de inicialização: o resto já loga com request id, e
    ``ext.api`` registra as rotas, que dependem de todo o resto pronto."""
    extensoes = list(_perfil(monkeypatch, nome)["EXTENSIONS"])

    assert extensoes[0] == "ext.observability"
    if "ext.api" in extensoes:
        assert extensoes[-1] == "ext.api"


@pytest.mark.parametrize("nome", ("consumer", "setup"))
def test_processo_sem_http_nao_registra_rota(monkeypatch, nome):
    assert list(_perfil(monkeypatch, nome)["API_V1_ROUTES"]) == []


@pytest.mark.parametrize("nome", ("default", *PERFIS))
def test_toda_dependencia_critica_do_health_e_carregada_no_perfil(monkeypatch, nome):
    """Falhou? O HEALTH_CRITICAL do perfil lista uma dependência que o processo
    não carrega. Ela nunca seria sondada, e a queda dela nunca daria 503. (Perfil
    sem rota herdaria o do [default]: declare ``HEALTH_CRITICAL = []``.)"""
    config = _perfil(monkeypatch, nome)
    extensoes = set(config["EXTENSIONS"])

    for dependencia in config["HEALTH_CRITICAL"]:
        assert EXTENSAO_DA_SONDA[dependencia] in extensoes, dependencia


def test_o_bronze_api_tira_o_processo_do_ar_sem_o_mongo(monkeypatch):
    """Sem o Mongo o bronze_api não serve nada; sem o broker, o api ainda serve
    login e cadastro."""
    assert set(_perfil(monkeypatch, "bronze_api")["HEALTH_CRITICAL"]) == {"database", "mongo"}
    assert set(_perfil(monkeypatch, "api")["HEALTH_CRITICAL"]) == {"database"}


def test_flask_process_desconhecido_reprova_a_inicializacao(monkeypatch):
    """O Dynaconf, sozinho, cairia calado no [default] e subiria a aplicação inteira."""
    monkeypatch.setenv(PROCESS_ENVVAR, "consumr")

    with pytest.raises(RuntimeError, match="não é um perfil"):
        build_config()


def test_sem_flask_process_vale_o_default(monkeypatch, tmp_path):
    # Sem .env (a raiz aponta para uma pasta vazia): um FLASK_PROCESS gravado no
    # .env de quem roda a suíte não pode decidir o resultado.
    monkeypatch.setattr(ext.config, "PROJECT_ROOT", tmp_path)
    default = list(_perfil(monkeypatch, "default")["EXTENSIONS"])
    monkeypatch.delenv(PROCESS_ENVVAR)

    assert list(build_config()["EXTENSIONS"]) == default


RAIZ = Path(__file__).resolve().parents[2]

#: Endereços numa porta fechada: montar a aplicação não conecta a nada, e o teste
#: reprovaria pela demora se conectasse.
MONGO_FECHADO = "mongodb://ninguem:nada@127.0.0.1:1/"
AMQP_FECHADO = "amqp://ninguem:nada@127.0.0.1:1/teste"


def _montar_num_processo_a_parte(perfil: str, imprime: str, **variaveis: str) -> str:
    """Monta a aplicação do ``perfil`` num processo novo e devolve o que ``imprime``
    (uma expressão Python sobre ``sys.modules``) escreveu.

    O ambiente não leva nenhuma ``FLASK_*`` da suíte, só as de ``variaveis``. O
    ``sys.path`` é o desta suíte, para que o processo importe do mesmo lugar.
    """
    ambiente = {
        chave: valor for chave, valor in os.environ.items() if not chave.startswith("FLASK_")
    }
    ambiente.update(variaveis, FLASK_PROCESS=perfil, PYTHONPATH=os.pathsep.join(sys.path))
    codigo = f"import sys\nfrom app import create_app\ncreate_app()\nprint({imprime})\n"

    resultado = subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=RAIZ,
        env=ambiente,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert resultado.returncode == 0, resultado.stderr[-2000:]
    return resultado.stdout.strip()


def test_o_consumer_sobe_sem_postgres_sem_jwt_e_sem_importar_os_dois():
    """O consumer monta a aplicação sem URI do banco e sem chave JWT, e nem
    importa as bibliotecas deles nem a camada HTTP (``blueprint``). Num processo
    à parte: na suíte, o SQLAlchemy já está carregado."""
    proibidos = {"sqlalchemy", "flask_sqlalchemy", "flask_jwt_extended", "psycopg", "blueprint"}

    importados = _montar_num_processo_a_parte(
        "consumer",
        f'",".join(sorted({proibidos!r} & set(sys.modules)))',
        FLASK_MONGO_URI=MONGO_FECHADO,
        FLASK_AMQP_URL=AMQP_FECHADO,
    )

    assert importados == "", f"o consumer importou: {importados}"


def test_o_processo_so_importa_as_rotas_do_proprio_perfil():
    """O container liga (e, para isso, importa) só as rotas do perfil. Com o
    pacote inteiro no wiring, o bronze_api importaria a rota de item, e o api,
    um dia, a de leitura do bronze."""
    rotas = _montar_num_processo_a_parte(
        "bronze_api",
        '",".join(sorted(m for m in sys.modules if m.startswith("blueprint.restapi.v1.")))',
        FLASK_SQLALCHEMY_DATABASE_URI="postgresql+psycopg://ninguem:nada@127.0.0.1:1/nada",
        FLASK_SECRET_KEY="chave-do-teste-de-composicao-sem-valor-0001",
        FLASK_JWT_SECRET_KEY="chave-jwt-do-teste-de-composicao-sem-valor-0002",
        FLASK_MONGO_URI=MONGO_FECHADO,
    )

    assert rotas == "blueprint.restapi.v1.auth,blueprint.restapi.v1.health"
