"""Fronteiras de import, lidas do código-fonte (``ast``), sem importar nada.

Ler em vez de importar é de propósito: o teste não pode carregar justamente o
que ele proíbe. O preço é não enxergar import dinâmico (``importlib``), que o
projeto não usa.

Hoje guarda as regras do domínio, escritas como listas de permitidos: o que não
está nelas reprova, inclusive a biblioteca que ninguém lembrou de proibir. O
``domain/`` tem duas partes: o ORM (``ORM``, abaixo) e o vocabulário, que é todo
o resto. Arquivo novo de vocabulário cai sozinho na regra estrita; só escapa
dela quem tem o nome na lista do ORM.
"""

import ast
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

#: Os únicos módulos do domínio que podem usar o SQLAlchemy.
ORM = {"domain.base", "domain.models"}

#: Fora do domínio, a folha só importa a stdlib e o marshmallow (o contrato da fila).
PERMITIDOS_NA_FOLHA = sys.stdlib_module_names | {"marshmallow"}

#: O ORM importa o SQLAlchemy e o próprio domínio; nenhuma outra camada.
PERMITIDOS_NO_ORM = sys.stdlib_module_names | {"sqlalchemy", "domain"}


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


FOLHAS_DO_DOMINIO = sorted(
    arquivo for arquivo in (APP / "domain").rglob("*.py") if _nome_do_modulo(arquivo) not in ORM
)


def test_o_dominio_tem_folhas_para_conferir():
    """Sem isto, um caminho errado faria o teste abaixo passar sem olhar nada."""
    assert {_nome_do_modulo(a) for a in FOLHAS_DO_DOMINIO} >= {
        "domain",
        "domain.authz",
        "domain.ports",
        "domain.vin",
    }


@pytest.mark.parametrize("arquivo", FOLHAS_DO_DOMINIO, ids=_nome_do_modulo)
def test_folha_do_dominio_so_importa_stdlib_marshmallow_e_outras_folhas(arquivo):
    """Falhou? Uma folha do domínio passou a depender de outra camada, do ORM ou de
    uma biblioteca. Ela é importada por qualquer camada e qualquer processo (o
    consumer importa ``domain.ports`` e não pode carregar o SQLAlchemy por tabela).
    Se o arquivo precisa disso, ele não é vocabulário: leve-o para a camada certa."""
    assert _proibidos_na_folha(_importados(arquivo)) == []


def test_o_init_do_dominio_nao_importa_nada():
    """``import domain.ports`` executa este ``__init__``: um import aqui seria
    carregado por todo consumidor das folhas."""
    assert _importados(APP / "domain" / "__init__.py") == set()


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
