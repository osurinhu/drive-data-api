"""Vocabulário do VIN: a forma canônica e a regra do formato."""

import pytest

from domain.vin import is_valid_vin, normalize_vin

#: O VIN do payload de exemplo (Renault do Brasil). O H na 9ª posição não passa
#: na conta do dígito verificador norte-americano, que não é exigido aqui.
VIN_DO_EXEMPLO = "93YBSR7RHEJ298116"


def test_o_vin_do_exemplo_e_valido_sem_conferir_o_digito_verificador():
    assert is_valid_vin(VIN_DO_EXEMPLO)


def test_normalizar_tira_os_espacos_das_pontas_e_poe_em_maiusculas():
    assert normalize_vin("  93ybsr7rhej298116\t") == VIN_DO_EXEMPLO


@pytest.mark.parametrize("vin", ["93ybsr7rhej298116", " 93YBSR7RHEJ298116 "])
def test_minusculas_e_espacos_nas_pontas_sao_aceitos(vin):
    assert is_valid_vin(vin)


@pytest.mark.parametrize(
    "vin",
    [
        pytest.param("93YBSR7RHEJ29811", id="16 caracteres"),
        pytest.param("93YBSR7RHEJ2981160", id="18 caracteres"),
        pytest.param("93YBSR7RHEJ29811I", id="com I"),
        pytest.param("93YBSR7RHEJ29811O", id="com O"),
        pytest.param("93YBSR7RHEJ29811q", id="com q minúsculo"),
        pytest.param("93YBSR7RH-J298116", id="com hífen"),
        pytest.param("93YBSR7RH J298116", id="com espaço no meio"),
        # Sem a checagem de ASCII, o upper() faria destes dois um VIN válido.
        pytest.param("93YBSR7RHEJ29811ſ", id="ſ, cujo upper() é S"),
        pytest.param("93YBSR7RHEJ2981ß", id="ß, cujo upper() é SS"),
        pytest.param("", id="vazio"),
    ],
)
def test_vin_fora_do_formato_e_recusado(vin):
    assert not is_valid_vin(vin)
