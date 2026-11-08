"""Fronteiras de import, lidas do código-fonte (``ast``), sem importar nada.

Ler em vez de importar é de propósito: o teste não pode carregar justamente o
que ele proíbe. O preço é não enxergar import dinâmico (``importlib``), que o
projeto não usa.

Guarda dois tipos de regra.

* **Os imports de cada módulo**, para o domínio e o pipeline de telemetria,
  escritos como listas de permitidos: o que não está nelas reprova, inclusive a
  biblioteca que ninguém lembrou de proibir. O ``domain/`` tem duas partes: o ORM
  (``ORM``, abaixo) e o vocabulário, que é todo o resto. Arquivo novo de
  vocabulário cai sozinho na regra estrita; só escapa dela quem tem o nome na
  lista do ORM. O mesmo vale para o arquivo novo de ``service/telemetry/``.
* **O fecho de imports** (tudo o que um módulo carrega, direta ou indiretamente)
  dos dois lados do pipeline e das duas tecnologias do repositório: o consumer
  não alcança o lado HTTP nem o SQL, o producer não alcança o Mongo, e cada
  pasta do repositório fica na sua tecnologia. A fila é a única ligação entre o
  producer e o consumer.
"""

import ast
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

#: Os únicos módulos do domínio que podem usar o SQLAlchemy.
ORM = {"domain.base", "domain.models"}

#: Fora do domínio, a folha só importa a stdlib e o marshmallow (o contrato da fila).
PERMITIDOS_NA_FOLHA = sys.stdlib_module_names | {"marshmallow"}

#: O ORM importa o SQLAlchemy e o próprio domínio; nenhuma outra camada.
PERMITIDOS_NO_ORM = sys.stdlib_module_names | {"sqlalchemy", "domain"}

#: O pipeline de telemetria só fala com portas e folhas, e com o vocabulário comum
#: do serviço. Nenhuma infraestrutura: os adaptadores chegam pelo construtor.
PERMITIDOS_NO_PIPELINE = {"service.errors", "service.pagination"}


def _nome_do_modulo(arquivo: Path) -> str:
    partes = arquivo.relative_to(APP).with_suffix("").parts
    return ".".join(partes[:-1] if partes[-1] == "__init__" else partes)


def _importados(arquivo: Path) -> set[str]:
    """Os módulos que o arquivo importa, absolutos (o projeto não usa import relativo).

    Do ``from pacote import nome`` entram os dois, ``pacote`` e ``pacote.nome``: é o
    que pega o ``from domain import models``.
    """
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"), filename=str(arquivo))
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(alias.name for alias in no.names)
        elif isinstance(no, ast.ImportFrom):
            assert no.level == 0, f"{arquivo}: import relativo"
            nomes.add(no.module or "")
            nomes.update(f"{no.module}.{alias.name}" for alias in no.names)
    return nomes


def _e_do_orm(nome: str) -> bool:
    return any(nome == orm or nome.startswith(f"{orm}.") for orm in ORM)


def _e_folha(nome: str) -> bool:
    return (nome == "domain" or nome.startswith("domain.")) and not _e_do_orm(nome)


def _proibidos_na_folha(importados: set[str]) -> list[str]:
    return sorted(
        nome
        for nome in importados
        if not _e_folha(nome) and nome.split(".")[0] not in PERMITIDOS_NA_FOLHA
    )


def _proibidos_no_orm(importados: set[str]) -> list[str]:
    return sorted(nome for nome in importados if nome.split(".")[0] not in PERMITIDOS_NO_ORM)


def _proibidos_no_pipeline(importados: set[str]) -> list[str]:
    def permitido(nome: str) -> bool:
        return (
            nome.split(".")[0] in sys.stdlib_module_names
            or _e_folha(nome)
            or nome == "service"
            or nome.startswith("service.telemetry")
            or any(nome == p or nome.startswith(f"{p}.") for p in PERMITIDOS_NO_PIPELINE)
        )

    return sorted(nome for nome in importados if not permitido(nome))


FOLHAS_DO_DOMINIO = sorted(
    arquivo for arquivo in (APP / "domain").rglob("*.py") if _nome_do_modulo(arquivo) not in ORM
)
PIPELINE = sorted((APP / "service" / "telemetry").rglob("*.py"))


def test_o_dominio_tem_folhas_para_conferir():
    """Sem isto, um caminho errado faria o teste abaixo passar sem olhar nada."""
    assert {_nome_do_modulo(a) for a in FOLHAS_DO_DOMINIO} >= {
        "domain",
        "domain.authz",
        "domain.contracts",
        "domain.contracts.telemetry_batch",
        "domain.ports",
        "domain.vin",
    }
    assert {_nome_do_modulo(a) for a in PIPELINE} >= {
        "service.telemetry.ingest_service",
        "service.telemetry.bronze_read_service",
    }


@pytest.mark.parametrize("arquivo", FOLHAS_DO_DOMINIO, ids=_nome_do_modulo)
def test_folha_do_dominio_so_importa_stdlib_marshmallow_e_outras_folhas(arquivo):
    """Falhou? Uma folha do domínio passou a depender de outra camada, do ORM ou de
    uma biblioteca. Ela é importada por qualquer camada e qualquer processo (o
    consumer importa ``domain.ports`` e não pode carregar o SQLAlchemy por tabela).
    Se o arquivo precisa disso, ele não é vocabulário: leve-o para a camada certa."""
    assert _proibidos_na_folha(_importados(arquivo)) == []


@pytest.mark.parametrize("pacote", ["domain", "domain/contracts", "service/telemetry"])
def test_o_init_dos_pacotes_de_vocabulario_e_do_pipeline_nao_importa_nada(pacote):
    """``import domain.ports`` executa o ``__init__`` de ``domain``: um import ali
    seria carregado por todo consumidor das folhas. No ``service/telemetry``, um
    import juntaria os dois lados do pipeline num processo só."""
    assert _importados(APP / pacote / "__init__.py") == set()


#: Carregados pelos três processos: é o que o ``/metrics`` e os adaptadores têm em comum.
OBSERVACAO = ("correlation", "logs", "metrics", "consumer_health")


@pytest.mark.parametrize("modulo", OBSERVACAO)
def test_a_observacao_so_importa_stdlib_prometheus_folhas_e_a_si_mesma(modulo):
    """Falhou? O módulo é comum aos dois lados da fila: uma dependência de infraestrutura
    aqui chegaria ao processo que não pode tê-la (invariante 8). Quem precisa do Mongo,
    do pika ou do Flask é adaptador ou extensão, e não instrumentação."""
    importados = _importados(APP / "instrumentation" / f"{modulo}.py")

    def permitido(nome: str) -> bool:
        raiz = nome.split(".")[0]
        return (
            raiz in sys.stdlib_module_names
            or raiz in {"prometheus_client", "instrumentation"}
            or _e_folha(nome)
        )

    assert sorted(nome for nome in importados if not permitido(nome)) == []


@pytest.mark.parametrize("modulo", sorted(ORM))
def test_o_orm_so_importa_sqlalchemy_e_o_dominio(modulo):
    """O ORM mapeia tabela para classe. Se ele precisa de serviço, repositório ou
    extensão, a lógica está na camada errada (01-arquitetura, a seta nunca aponta
    para cima)."""
    arquivo = APP.joinpath(*modulo.split(".")).with_suffix(".py")

    assert _proibidos_no_orm(_importados(arquivo)) == []


@pytest.mark.parametrize(
    ("importados", "proibidos"),
    [
        pytest.param({"re", "domain.vin"}, [], id="stdlib e outra folha"),
        pytest.param({"marshmallow"}, [], id="marshmallow (o contrato da fila)"),
        pytest.param(
            {"domain.models", "domain.models.User"},
            ["domain.models", "domain.models.User"],
            id="o ORM",
        ),
        pytest.param({"domain", "domain.base"}, ["domain.base"], id="from domain import base"),
        pytest.param({"sqlalchemy.orm"}, ["sqlalchemy.orm"], id="infraestrutura"),
        pytest.param({"psycopg", "werkzeug"}, ["psycopg", "werkzeug"], id="biblioteca qualquer"),
        pytest.param({"service.errors"}, ["service.errors"], id="outra camada"),
    ],
)
def test_a_regra_da_folha(importados, proibidos):
    """A regra, provada sobre exemplos: sem isto, um teste que nunca reprova pareceria
    um domínio bem comportado."""
    assert _proibidos_na_folha(importados) == proibidos


@pytest.mark.parametrize("arquivo", PIPELINE, ids=_nome_do_modulo)
def test_o_pipeline_so_importa_portas_folhas_e_o_vocabulario_do_servico(arquivo):
    """Falhou? Um módulo de ``service/telemetry/`` passou a importar infraestrutura
    (``flask``, ``pika``, ``pymongo``, ``sqlalchemy``), um adaptador ou o ORM. A
    regra deixaria de se testar com dublês, e o consumer, que importa o seu lado
    do pipeline, carregaria o que não usa. O adaptador chega pelo construtor."""
    assert _proibidos_no_pipeline(_importados(arquivo)) == []


@pytest.mark.parametrize(
    ("importados", "proibidos"),
    [
        pytest.param(
            {"uuid", "domain.ports", "service.errors", "service.telemetry.ingest_rules"},
            [],
            id="o permitido",
        ),
        pytest.param({"pika"}, ["pika"], id="infraestrutura"),
        pytest.param({"domain.models"}, ["domain.models"], id="o ORM"),
        pytest.param(
            {"repository.sql.vehicle_repository"},
            ["repository.sql.vehicle_repository"],
            id="um adaptador",
        ),
        pytest.param({"service.base_service"}, ["service.base_service"], id="o CRUD da base"),
    ],
)
def test_a_regra_do_pipeline(importados, proibidos):
    assert _proibidos_no_pipeline(importados) == proibidos


@pytest.mark.parametrize(
    ("importados", "proibidos"),
    [
        pytest.param(
            {"datetime", "sqlalchemy.orm", "domain.base", "domain.authz"}, [], id="o permitido"
        ),
        pytest.param({"ext.database"}, ["ext.database"], id="extensão"),
        pytest.param({"service.errors"}, ["service.errors"], id="outra camada"),
        pytest.param({"flask_sqlalchemy"}, ["flask_sqlalchemy"], id="outra biblioteca"),
    ],
)
def test_a_regra_do_orm(importados, proibidos):
    assert _proibidos_no_orm(importados) == proibidos


# --- Fechos de import -----------------------------------------------------------

#: O processo consumer, a partir de todas as extensões do perfil dele: um import
#: dentro de uma função de qualquer uma (só um caminho raro o executa) também conta.
CONSUMER = tuple(
    tomllib.loads((APP.parent / "settings.toml").read_text(encoding="utf-8"))["consumer"][
        "EXTENSIONS"
    ]
)
FORA_DO_CONSUMER = (
    "blueprint",
    "ext.auth",
    "ext.container",
    "ext.database",
    "repository.sql",
    "domain.base",
    "domain.models",
    "service.telemetry.ingest_",
    "messaging.publisher",
    # O lado da silver: a leitura do bronze é do bronze-api.
    "service.telemetry.bronze_read_service",
    "repository.mongo.bronze_reader",
    "sqlalchemy",
    "flask_sqlalchemy",
    "flask_jwt_extended",
)

#: O lado do producer no pipeline: a rota, o serviço e o publisher. O
#: ``ext.container`` não é seguido: é a raiz de composição dos dois processos HTTP e
#: monta também o leitor do bronze, do ``bronze-api``. Quem decide o que cada processo
#: conecta é o perfil (``EXTENSIONS``), e o ``test_composicao.py`` confere o processo
#: montado.
PRODUCER = (
    "blueprint.restapi.v1.telemetry",
    "service.telemetry.ingest_service",
    "messaging.publisher",
)
FORA_DO_PRODUCER = (
    "ext.mongo",
    "ext.consumer",
    "repository.mongo",
    "service.telemetry.bronze_",
    "messaging.consumer",
    "pymongo",
)

#: O lado da silver (o bronze-api): a rota de leitura, o serviço e o leitor. O
#: ``ext.container`` não é seguido, pelo mesmo motivo do producer.
SILVER = (
    "blueprint.restapi.v1.bronze",
    "service.telemetry.bronze_read_service",
    "repository.mongo.bronze_reader",
)
FORA_DA_SILVER = (
    "ext.rabbitmq",
    "ext.consumer",
    "messaging",
    "pika",
    "service.telemetry.ingest_",
    "service.telemetry.bronze_ingest_service",
    "service.telemetry.bronze_rules",
    "repository.mongo.bronze_writer",
    "repository.mongo.rejection_log",
)

#: Cada pasta do repositório fica na sua tecnologia, e nenhuma sobe para o serviço.
FORA_DA_TECNOLOGIA = {
    "sql": ("pymongo", "ext.mongo", "repository.mongo", "service"),
    "mongo": (
        "sqlalchemy",
        "flask_sqlalchemy",
        "ext.database",
        "repository.sql",
        "domain.base",
        "domain.models",
        "service",
    ),
}


def _arquivo_do_modulo(nome: str) -> Path | None:
    caminho = APP.joinpath(*nome.split("."))
    if caminho.with_suffix(".py").is_file():
        return caminho.with_suffix(".py")
    if (caminho / "__init__.py").is_file():
        return caminho / "__init__.py"
    return None


def _fecho(inicio: Iterable[str], *, sem_seguir: Iterable[str] = ()) -> set[str]:
    """Os módulos do projeto alcançados a partir de ``inicio``, e os nomes de
    biblioteca que eles importam (``pika``, ``sqlalchemy.orm``).

    Importar ``a.b.c`` executa também ``a`` e ``a.b``. Um nome de ``sem_seguir``
    entra no fecho, mas os imports dele, não.
    """
    nao_seguir = set(sem_seguir)
    vistos: set[str] = set()
    pendentes = list(inicio)
    while pendentes:
        nome = pendentes.pop()
        if nome in vistos:
            continue
        vistos.add(nome)
        arquivo = _arquivo_do_modulo(nome)
        if arquivo is None or nome in nao_seguir:
            continue
        partes = nome.split(".")
        pendentes.extend(".".join(partes[:i]) for i in range(1, len(partes)))
        pendentes.extend(_importados(arquivo))
    return vistos


def _alcancados(fecho: set[str], proibidos: Iterable[str]) -> list[str]:
    """Os nomes do fecho que casam com algum proibido. ``"x."`` casa o pacote ``x`` e
    o que está dentro dele; um padrão terminado em ``_`` é prefixo de nome
    (``service.telemetry.ingest_`` casa ``ingest_rules`` e ``ingest_service``)."""

    def casa(nome: str, padrao: str) -> bool:
        if padrao.endswith("_"):
            return nome.startswith(padrao)
        return nome == padrao or nome.startswith(f"{padrao}.")

    return sorted(nome for nome in fecho if any(casa(nome, p) for p in proibidos))


def test_o_fecho_segue_os_imports_do_projeto():
    """Sem isto, um caminho errado faria os testes abaixo passarem sem olhar nada."""
    assert {"ext.consumer", "ext.consumer_container"} <= set(CONSUMER)
    assert _fecho(CONSUMER) >= {
        "ext.mongo",
        "repository.mongo.bronze_writer",
        "service.telemetry.bronze_ingest_service",
        "messaging.consumer",
        "domain.contracts.telemetry_batch",
        "pymongo",
        "pika",
    }
    assert _fecho(PRODUCER, sem_seguir={"ext.container"}) >= {
        "ext.auth",
        "repository.sql.user_repository",
        "messaging.connection",
        "ext.container",
    }
    assert _fecho(SILVER, sem_seguir={"ext.container"}) >= {
        "ext.auth",
        "ext.mongo",
        "repository.mongo.server",
        "pymongo",
        "ext.container",
    }


def test_o_consumer_nao_alcanca_o_lado_http_nem_o_sql():
    """Falhou? Um módulo do consumer passou a importar, direta ou indiretamente, o
    lado HTTP, o SQL ou o lado do producer. O processo consumer carregaria o que
    não usa e passaria a depender do Postgres ou do JWT. O que os dois lados
    compartilham são as folhas do domínio e a conexão com o broker
    (``messaging.connection``); o resto atravessa a fila."""
    assert _alcancados(_fecho(CONSUMER), FORA_DO_CONSUMER) == []


def test_o_producer_nao_alcanca_o_bronze():
    """Falhou? O lado do producer passou a importar o Mongo ou o lado do consumer.
    A queda do Mongo derrubaria a ingestão, que só depende da fila."""
    assert _alcancados(_fecho(PRODUCER, sem_seguir={"ext.container"}), FORA_DO_PRODUCER) == []


def test_a_leitura_da_silver_nao_alcanca_a_fila_nem_a_gravacao():
    """Falhou? O lado da silver passou a importar o RabbitMQ ou o lado do consumer.
    O bronze-api não conhece a fila: só lê o que o consumer já gravou."""
    assert _alcancados(_fecho(SILVER, sem_seguir={"ext.container"}), FORA_DA_SILVER) == []


@pytest.mark.parametrize("tecnologia", sorted(FORA_DA_TECNOLOGIA))
def test_cada_pasta_do_repositorio_fica_na_sua_tecnologia(tecnologia):
    """Falhou? Um adaptador SQL alcançou o Mongo, ou o contrário, ou um adaptador
    subiu para o serviço. O consumer importa ``repository.mongo`` e nunca pode
    carregar o SQL por tabela."""
    pasta = APP / "repository" / tecnologia
    arquivos = sorted(pasta.rglob("*.py"))
    assert arquivos, pasta

    for arquivo in arquivos:
        fecho = _fecho([_nome_do_modulo(arquivo)])
        assert _alcancados(fecho, FORA_DA_TECNOLOGIA[tecnologia]) == [], arquivo


@pytest.mark.parametrize(
    ("nomes", "alcancados"),
    [
        pytest.param(
            {"repository.sql", "repository.sql.user"},
            ["repository.sql", "repository.sql.user"],
            id="o pacote e o que está nele",
        ),
        pytest.param({"repository.sqlite"}, [], id="outro nome com o mesmo começo"),
        pytest.param(
            {"service.telemetry.ingest_rules"},
            ["service.telemetry.ingest_rules"],
            id="prefixo de nome",
        ),
    ],
)
def test_a_regra_do_fecho(nomes, alcancados):
    assert _alcancados(nomes, ("repository.sql", "service.telemetry.ingest_")) == alcancados
