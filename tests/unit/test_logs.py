"""O formato dos logs e os ids de correlação, sem aplicação."""

import json
import logging
import sys
from uuid import UUID

import pytest

from instrumentation.correlation import (
    bind_correlation,
    clear_correlation,
    current_correlation,
    resolve_request_id,
)
from instrumentation.logs import JsonFormatter, configure_logging


@pytest.fixture(autouse=True)
def _sem_correlacao():
    clear_correlation()
    yield
    clear_correlation()


def _registro(mensagem="oi", *args, exc_info=None, **extra):
    registro = logging.LogRecord("o.logger", logging.WARNING, "x.py", 1, mensagem, args, exc_info)
    registro.__dict__.update(extra)
    return registro


def _linha(registro) -> dict:
    return json.loads(JsonFormatter("consumer").format(registro))


def test_a_linha_traz_os_campos_fixos():
    linha = _linha(_registro("lote %d", 7))

    assert linha["level"] == "WARNING"
    assert linha["logger"] == "o.logger"
    assert linha["message"] == "lote 7"
    assert linha["process"] == "consumer"
    assert linha["ts"].endswith("Z")


def test_a_linha_traz_a_correlacao_e_omite_o_que_nao_existe():
    bind_correlation(request_id="req-1", user_id=51)

    linha = _linha(_registro())

    assert (linha["request_id"], linha["user_id"]) == ("req-1", 51)
    assert "message_id" not in linha


def test_os_campos_do_evento_entram_na_linha_e_valor_nao_json_vira_texto():
    id_ = UUID(int=1)

    linha = _linha(_registro(event="bronze.record.collision", record_id=id_))

    assert linha["event"] == "bronze.record.collision"
    assert linha["record_id"] == str(id_)


def test_quebra_de_linha_na_mensagem_nao_forja_outra_linha_de_log():
    saida = JsonFormatter("api").format(_registro('x\n{"level": "CRITICAL"}'))

    assert "\n" not in saida
    assert json.loads(saida)["message"] == 'x\n{"level": "CRITICAL"}'


def test_a_excecao_entra_na_linha():
    try:
        raise ValueError("deu ruim")
    except ValueError:
        linha = _linha(_registro(exc_info=sys.exc_info()))

    assert "ValueError: deu ruim" in linha["exception"]


def test_formato_desconhecido_reprova_a_subida():
    with pytest.raises(RuntimeError, match="LOG_FORMAT"):
        configure_logging("INFO", "xml", process="api")


def test_limpar_apaga_os_ids():
    bind_correlation(request_id="r", message_id="m", user_id=1)

    clear_correlation()

    assert current_correlation() == {}


@pytest.mark.parametrize(
    ("recebido", "esperado"),
    [("abc-1.2_x", "abc-1.2_x"), ("a b\nc", "abc"), ("x" * 100, "x" * 64)],
    ids=["válido", "caracteres perigosos", "longo demais"],
)
def test_o_id_do_cliente_e_higienizado(recebido, esperado):
    assert resolve_request_id(recebido) == esperado


@pytest.mark.parametrize("recebido", [None, "", "\n\n"], ids=["ausente", "vazio", "só lixo"])
def test_sem_id_valido_nasce_um_novo(recebido):
    assert len(resolve_request_id(recebido)) == 32
