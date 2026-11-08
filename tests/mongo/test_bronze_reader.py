"""O leitor do bronze (``MongoBronzeReader``) contra o Mongo de verdade.

Os registros entram pelo writer de verdade: a leitura devolve o que a gravação
guardou. O contexto de aplicação que os dois exigem (``mongo.db``) vem do conftest
desta pasta.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from domain.authz import ANY_OWNER
from domain.ports import (
    BRONZE_START,
    NO_FILTERS,
    BronzeEntry,
    BronzeFilters,
    BronzePosition,
    BronzeUnavailable,
)
from repository.mongo import indexes
from repository.mongo.bronze_document import RECORDS
from repository.mongo.bronze_reader import ORDER, MongoBronzeReader, page_query
from repository.mongo.bronze_writer import MongoBronzeWriter

#: Milissegundos: a precisão da data BSON.
T0 = datetime(2026, 11, 7, 14, 0, 0, 482000, tzinfo=UTC)
DEPOIS_DE_TUDO = T0 + timedelta(days=1)
VIN = "93YBSR7RHEJ298116"
OUTRO_VIN = "9BWZZZ377VT004251"


def _registro(
    ingested_at: datetime,
    *,
    user_id: int = 51,
    record_id: UUID | None = None,
    vin: str = VIN,
    metricas: tuple[str, ...] = ("rpm",),
    coletado: datetime | None = None,
):
    record_id = record_id or uuid4()
    return BronzeEntry(
        record_id=record_id,
        vin=vin,
        vehicle_id=7,
        user_id=user_id,
        sender_relation="owner",
        payload_user_matches_sender=True,
        collected_at=coletado or ingested_at - timedelta(minutes=2),
        available_metrics=metricas,
        received_at=ingested_at - timedelta(seconds=1),
        ingested_at=ingested_at,
        message_id=uuid4(),
        request_id="req-1",
        schema_version=1,
        raw={"id": str(record_id), "readings": [{"metric": "rpm", "value": 1065.0}]},
    )


def _posicao(entry: BronzeEntry) -> BronzePosition:
    return BronzePosition(ingested_at=entry.ingested_at, record_id=entry.record_id)


def _ordem(entry: BronzeEntry):
    return (entry.ingested_at, str(entry.record_id))


@pytest.fixture
def gravar():
    writer = MongoBronzeWriter(timeout=5)

    def _gravar(*registros: BronzeEntry) -> list[BronzeEntry]:
        assert writer.insert_new(registros) == set()
        return sorted(registros, key=_ordem)

    return _gravar


@pytest.fixture
def reader():
    return MongoBronzeReader(timeout=5)


def _ler(
    reader,
    after=BRONZE_START,
    *,
    until=DEPOIS_DE_TUDO,
    limit=100,
    owner_scope=ANY_OWNER,
    filters=NO_FILTERS,
):
    return reader.read_after(
        after, until=until, limit=limit, owner_scope=owner_scope, filters=filters
    )


def test_le_o_que_o_writer_gravou_campo_a_campo(gravar, reader):
    [registro] = gravar(_registro(T0))

    assert _ler(reader) == [registro]


def test_le_em_ordem_de_gravacao_e_desempata_pelo_id(gravar, reader):
    """Três registros no mesmo milissegundo saem na ordem do ``_id``, entre o anterior
    e o seguinte."""
    gravados = gravar(
        _registro(T0 + timedelta(seconds=2)),
        *(_registro(T0 + timedelta(seconds=1)) for _ in range(3)),
        _registro(T0),
    )

    assert _ler(reader) == gravados


def test_comeca_estritamente_depois_da_posicao_mesmo_no_empate(gravar, reader):
    primeiro, *empatados, ultimo = gravar(
        _registro(T0),
        *(_registro(T0 + timedelta(seconds=1)) for _ in range(3)),
        _registro(T0 + timedelta(seconds=2)),
    )

    assert _ler(reader, _posicao(primeiro)) == [*empatados, ultimo]
    assert _ler(reader, _posicao(empatados[0])) == [*empatados[1:], ultimo]
    assert _ler(reader, _posicao(ultimo)) == []


def test_o_limite_superior_e_inclusivo(gravar, reader):
    antes, no_limite, depois = gravar(
        _registro(T0), _registro(T0 + timedelta(seconds=1)), _registro(T0 + timedelta(seconds=2))
    )

    assert _ler(reader, until=no_limite.ingested_at) == [antes, no_limite]
    assert depois not in _ler(reader, until=no_limite.ingested_at)


def test_o_limite_de_registros(gravar, reader):
    gravados = gravar(*(_registro(T0 + timedelta(seconds=i)) for i in range(5)))

    assert _ler(reader, limit=2) == gravados[:2]


def test_limite_zero_e_recusado(reader):
    """No PyMongo, ``limit=0`` é "sem limite": a página viria com o bronze inteiro."""
    with pytest.raises(ValueError):
        _ler(reader, limit=0)


def test_o_escopo_de_dono_filtra_pelo_remetente(gravar, reader):
    meu, alheio = gravar(
        _registro(T0, user_id=51), _registro(T0 + timedelta(seconds=1), user_id=52)
    )

    assert _ler(reader, owner_scope=51) == [meu]
    assert _ler(reader, owner_scope=ANY_OWNER) == [meu, alheio]


def test_o_filtro_de_vin(gravar, reader):
    gravados = gravar(_registro(T0, vin=OUTRO_VIN), _registro(T0, vin=VIN))
    [procurado] = [registro for registro in gravados if registro.vin == OUTRO_VIN]

    assert _ler(reader, filters=BronzeFilters(vin=OUTRO_VIN)) == [procurado]


def test_o_filtro_de_metrica_casa_qualquer_uma_e_nao_repete_o_registro(gravar, reader):
    """O registro com as duas métricas aparece duas vezes no índice multikey."""
    gravados = gravar(
        _registro(T0, metricas=("rpm", "speed")),
        _registro(T0 + timedelta(seconds=1), metricas=("speed",)),
        _registro(T0 + timedelta(seconds=2), metricas=("maf",)),
        _registro(T0 + timedelta(seconds=3), metricas=()),
    )

    lidos = _ler(reader, filters=BronzeFilters(metrics=("rpm", "speed")))

    assert lidos == gravados[:2]


def test_o_periodo_de_coleta_e_inclusivo_nas_duas_pontas(gravar, reader):
    antes, inicio, fim, depois = gravar(
        _registro(T0, coletado=T0),
        _registro(T0 + timedelta(seconds=1), coletado=T0 + timedelta(hours=1)),
        _registro(T0 + timedelta(seconds=2), coletado=T0 + timedelta(hours=2)),
        _registro(T0 + timedelta(seconds=3), coletado=T0 + timedelta(hours=3)),
    )
    periodo = BronzeFilters(collected_from=inicio.collected_at, collected_to=fim.collected_at)

    assert _ler(reader, filters=periodo) == [inicio, fim]
    assert _ler(reader, filters=BronzeFilters(collected_from=fim.collected_at)) == [fim, depois]
    assert _ler(reader, filters=BronzeFilters(collected_to=antes.collected_at)) == [antes]


def test_os_filtros_se_combinam(gravar, reader):
    [procurado, *_] = gravar(
        _registro(T0, vin=OUTRO_VIN, metricas=("speed",)),
        _registro(T0 + timedelta(seconds=1), vin=OUTRO_VIN, metricas=("rpm",)),
        _registro(T0 + timedelta(seconds=2), vin=VIN, metricas=("speed",)),
    )

    assert _ler(reader, filters=BronzeFilters(vin=OUTRO_VIN, metrics=("speed",))) == [procurado]


@pytest.mark.parametrize(
    "filtros",
    [
        pytest.param(BronzeFilters(vin=OUTRO_VIN), id="por VIN"),
        pytest.param(BronzeFilters(metrics=("speed", "maf")), id="por métricas"),
        pytest.param(BronzeFilters(collected_from=T0), id="por período"),
    ],
)
def test_filtrando_le_tudo_uma_vez_so_mesmo_com_empate_no_milissegundo(gravar, reader, filtros):
    """Com páginas de um, nenhum registro gravado no mesmo milissegundo que outro se
    perde ou se repete, inclusive com um registro de fora do filtro entre eles."""
    procurados = [
        _registro(T0 + timedelta(seconds=1), vin=OUTRO_VIN, metricas=("speed", "maf"), coletado=T0)
        for _ in range(4)
    ]
    de_fora = [
        _registro(T0 + timedelta(seconds=1), metricas=("rpm",), coletado=T0 - timedelta(hours=1))
        for _ in range(4)
    ]
    gravar(*procurados, *de_fora)

    lidos, posicao = [], BRONZE_START
    while pagina := _ler(reader, posicao, limit=1, filters=filtros):
        lidos += pagina
        posicao = _posicao(pagina[-1])

    assert lidos == sorted(procurados, key=_ordem)


def _estagios(plano: Any) -> list[dict]:
    """Os estágios de um plano do ``explain``, em qualquer profundidade."""
    if isinstance(plano, list):
        return [estagio for item in plano for estagio in _estagios(item)]
    if not isinstance(plano, dict):
        return []
    proprio = [plano] if "stage" in plano else []
    return proprio + [e for valor in plano.values() for e in _estagios(valor)]


@pytest.mark.parametrize("owner_scope", [ANY_OWNER, 51], ids=["de todos", "de um dono"])
def test_a_pagina_sai_do_indice_sem_ordenar_em_memoria(gravar, mongo_db, owner_scope):
    """A ordem da leitura é a do ``ix_ingested``, e o intervalo do cursor é o limite da
    varredura: sem isso, cada página ordenaria a coleção inteira."""
    indexes.apply(mongo_db)
    gravados = gravar(*(_registro(T0 + timedelta(seconds=i)) for i in range(5)))
    consulta = page_query(
        _posicao(gravados[1]), until=DEPOIS_DE_TUDO, owner_scope=owner_scope, filters=NO_FILTERS
    )

    plano = mongo_db[RECORDS].find(consulta, sort=ORDER, limit=2).explain()

    estagios = _estagios(plano["queryPlanner"]["winningPlan"])
    nomes = {estagio["stage"] for estagio in estagios}
    assert "SORT" not in nomes, nomes
    assert {e.get("indexName") for e in estagios if e["stage"] == "IXSCAN"} == {indexes.INGESTED}


@pytest.mark.parametrize(
    ("filtros", "indice"),
    [
        pytest.param(BronzeFilters(vin=OUTRO_VIN), indexes.VIN_INGESTED, id="por VIN"),
        pytest.param(BronzeFilters(metrics=("speed",)), indexes.METRIC_INGESTED, id="uma métrica"),
        pytest.param(
            BronzeFilters(metrics=("maf", "speed")), indexes.METRIC_INGESTED, id="duas métricas"
        ),
        pytest.param(BronzeFilters(collected_from=T0), indexes.INGESTED, id="por período"),
    ],
)
def test_a_pagina_filtrada_sai_do_indice_sem_ordenar_em_memoria(gravar, mongo_db, filtros, indice):
    """A ordem é a do índice também com filtro: o ``$in`` de várias métricas é uma junção
    ordenada das faixas (``SORT_MERGE``), e não uma ordenação."""
    indexes.apply(mongo_db)
    # Os procurados ficam espalhados entre os outros: colados, a varredura pela ordem
    # acharia todos em seguida, e seria mesmo a melhor escolha.
    gravar(*(_registro(T0 + timedelta(seconds=i), metricas=("rpm",)) for i in range(60)))
    procurados = gravar(
        *(
            _registro(
                T0 + timedelta(seconds=12 * i, milliseconds=500),
                vin=OUTRO_VIN,
                metricas=("maf", "speed"),
            )
            for i in range(5)
        )
    )
    consulta = page_query(
        _posicao(procurados[1]), until=DEPOIS_DE_TUDO, owner_scope=ANY_OWNER, filters=filtros
    )

    plano = mongo_db[RECORDS].find(consulta, sort=ORDER, limit=2).explain()

    estagios = _estagios(plano["queryPlanner"]["winningPlan"])
    nomes = {estagio["stage"] for estagio in estagios}
    assert "SORT" not in nomes, nomes
    assert {e.get("indexName") for e in estagios if e["stage"] == "IXSCAN"} == {indice}


def test_a_hora_do_servidor_vem_com_fuso(reader):
    hora = reader.server_time()

    assert hora.tzinfo is not None
    assert abs(hora - datetime.now(UTC)) < timedelta(minutes=1)


def test_mongo_fora_do_ar_vira_bronze_indisponivel(mongo_avulso):
    fora = MongoBronzeReader(timeout=0.5)

    with mongo_avulso("leitura-sem-mongo", "mongodb://ninguem:nada@127.0.0.1:1/"):
        with pytest.raises(BronzeUnavailable):
            fora.server_time()
        with pytest.raises(BronzeUnavailable):
            _ler(fora)


def test_leitura_fora_do_prazo_vira_bronze_indisponivel(gravar):
    gravar(_registro(T0))
    sem_tempo = MongoBronzeReader(timeout=0.000001)

    with pytest.raises(BronzeUnavailable):
        _ler(sem_tempo)
