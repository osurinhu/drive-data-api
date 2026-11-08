"""As regras de formato do lote, uma a uma, só com o schema.

O caminho HTTP (422 no lote inteiro, 413 do corpo) está em ``tests/test_telemetry.py``.
"""

from uuid import uuid4

import pytest
from marshmallow import ValidationError

from schemas.telemetry_schema import TelemetryBatchSchema

VIN = "93YBSR7RHEJ298116"


def _leitura(**campos) -> dict:
    return {"metric": "rpm", "unit": "rpm", "available": True, "value": 1065.0, **campos}


def _registro(**campos) -> dict:
    return {
        "id": str(uuid4()),
        "vin": VIN,
        "collected_at": "2026-11-07T12:00:00.482Z",
        "readings": [_leitura()],
        **campos,
    }


def _erros(corpo: dict) -> dict:
    with pytest.raises(ValidationError) as erro:
        TelemetryBatchSchema().load(corpo)
    return erro.value.messages


def _erros_da_leitura(**campos) -> dict:
    return _erros({"records": [_registro(readings=[_leitura(**campos)])]})["records"][0][
        "readings"
    ][0]


def _erros_do_registro(**campos) -> dict:
    return _erros({"records": [_registro(**campos)]})["records"][0]


@pytest.mark.parametrize(
    "campos",
    [
        pytest.param({"available": False, "value": None}, id="indisponível, sem valor"),
        pytest.param({"value": 0}, id="zero é um valor"),
        pytest.param({"value": 900}, id="número inteiro"),
        pytest.param({"metric": "a" * 64}, id="métrica no tamanho máximo"),
        pytest.param({"unit": "u" * 16}, id="unidade no tamanho máximo"),
    ],
)
def test_a_leitura_valida_passa(campos):
    TelemetryBatchSchema().load({"records": [_registro(readings=[_leitura(**campos)])]})


@pytest.mark.parametrize("valor", ["true", "false", 1, 0, None], ids=repr)
def test_available_so_aceita_booleano(valor):
    assert list(_erros_da_leitura(available=valor)) == ["available"]


@pytest.mark.parametrize(
    "valor", ["1065.0", True, False, [1], float("nan"), float("inf")], ids=repr
)
def test_value_so_aceita_numero_ou_nulo(valor):
    assert list(_erros_da_leitura(value=valor)) == ["value"]


@pytest.mark.parametrize(
    ("disponivel", "valor"),
    [pytest.param(True, None, id="disponível sem valor"), (False, 12.5), (False, 0)],
)
def test_o_valor_acompanha_a_disponibilidade(disponivel, valor):
    assert list(_erros_da_leitura(available=disponivel, value=valor)) == ["value"]


@pytest.mark.parametrize(
    "metrica",
    ["RPM", "1rpm", "_rpm", "rp-m", "", "a" * 65, "rpm\n", "rotação"],
    ids=repr,
)
def test_o_nome_da_metrica_segue_o_padrao(metrica):
    assert list(_erros_da_leitura(metric=metrica)) == ["metric"]


def test_a_unidade_tem_ate_16_caracteres():
    assert list(_erros_da_leitura(unit="u" * 17)) == ["unit"]


@pytest.mark.parametrize("campo", ["metric", "unit", "available", "value"])
def test_todo_campo_da_leitura_e_obrigatorio(campo):
    leitura = _leitura()
    del leitura[campo]

    assert list(_erros({"records": [_registro(readings=[leitura])]})["records"][0]["readings"][0])


def test_a_metrica_nao_se_repete_no_registro():
    leituras = [_leitura(), _leitura(value=900.0)]

    assert list(_erros_do_registro(readings=leituras)) == ["readings"]


def test_a_mesma_metrica_pode_aparecer_em_registros_diferentes():
    TelemetryBatchSchema().load({"records": [_registro(), _registro()]})


def test_o_registro_tem_de_uma_a_64_leituras():
    leituras = [_leitura(metric=f"m{i}") for i in range(64)]

    TelemetryBatchSchema().load({"records": [_registro(readings=leituras)]})
    assert list(_erros_do_registro(readings=[*leituras, _leitura(metric="m64")])) == ["readings"]
    assert list(_erros_do_registro(readings=[])) == ["readings"]


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        pytest.param("id", "nao-e-uuid", id="id sem UUID"),
        pytest.param("id", 7, id="id numérico"),
        pytest.param("vin", "93YBSR7RHEJ29811O", id="VIN com O"),
        pytest.param("vin", "93YBSR7RHEJ29811", id="VIN curto"),
        pytest.param("collected_at", "2026-11-07T12:00:00", id="data sem fuso"),
        pytest.param("collected_at", 1762516800, id="data numérica"),
    ],
)
def test_os_campos_do_registro_seguem_o_formato(campo, valor):
    assert list(_erros_do_registro(**{campo: valor})) == [campo]


def test_campo_desconhecido_no_registro_e_na_leitura_chega_intacto_ao_bruto():
    registro = _registro(readings=[_leitura(firmware="1.2")], app_version="3.4", user="admin")

    [carregado] = TelemetryBatchSchema().load({"records": [registro]})["records"]

    assert carregado["raw"] == registro


def test_campo_desconhecido_na_raiz_reprova():
    assert list(_erros({"records": [_registro()], "extra": 1})) == ["extra"]


def test_o_lote_tem_de_um_a_500_registros():
    TelemetryBatchSchema().load({"records": [_registro() for _ in range(500)]})

    assert list(_erros({"records": [_registro() for _ in range(501)]})) == ["records"]
    assert list(_erros({"records": []})) == ["records"]


def test_o_id_nao_se_repete_no_lote():
    repetido = _registro()

    assert list(_erros({"records": [repetido, _registro(id=repetido["id"])]})) == ["records"]


def test_o_mesmo_id_em_outra_grafia_tambem_e_repetido():
    repetido = _registro()

    erros = _erros({"records": [repetido, _registro(id=repetido["id"].upper())]})

    assert list(erros) == ["records"]
