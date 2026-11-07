"""Vocabulário do VIN: a forma canônica e a regra do formato.

Módulo folha: não importa nada do projeto. O schema recusa o formato, o serviço
grava a forma canônica, e quem consulta a posse de um VIN usa a mesma forma.

O formato é o da ISO 3779 (17 caracteres, sem I, O e Q), **sem** o dígito
verificador: ele só é obrigatório na América do Norte, e um VIN brasileiro
válido como ``93YBSR7RHEJ298116`` seria recusado (ver `documentacao/02-decisoes.md`).
"""

import re

#: Letras maiúsculas e dígitos, sem I, O e Q (que se confundem com 1 e 0).
VIN_REGEX = re.compile(r"[A-HJ-NPR-Z0-9]{17}")

#: A regra em palavras, para quem recusa um VIN dizer o porquê.
INVALID_VIN_MESSAGE = "VIN inválido: são 17 letras e dígitos, sem I, O e Q."


def normalize_vin(value: str) -> str:
    """Forma canônica: sem espaços nas pontas e em maiúsculas."""
    return value.strip().upper()


def is_valid_vin(value: str) -> bool:
    """Verdadeiro se o valor, depois de normalizado, segue o formato.

    Aceita minúsculas e espaços nas pontas. Recusa qualquer caractere fora do
    ASCII antes do ``upper()``: ``"ſ".upper()`` é ``"S"`` e ``"ß".upper()`` é
    ``"SS"``, e um caractere assim viraria uma letra válida do VIN.
    """
    stripped = value.strip()
    return stripped.isascii() and VIN_REGEX.fullmatch(stripped.upper()) is not None
