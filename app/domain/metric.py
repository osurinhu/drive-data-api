"""Vocabulário das métricas de telemetria: a regra do nome.

Folha do domínio: não importa nada do projeto. O envio (producer) recusa o nome fora da
regra, e a leitura da silver filtra pelo mesmo vocabulário.
"""

import re

#: Letras minúsculas, dígitos e ``_``, começando por letra, até 64 caracteres.
METRIC_REGEX = re.compile(r"[a-z][a-z0-9_]{0,63}")

#: A regra em palavras, para quem recusa um nome dizer o porquê.
INVALID_METRIC_MESSAGE = "Use letras minúsculas, dígitos e _, começando por letra (até 64)."


def is_valid_metric(value: str) -> bool:
    # `fullmatch`: com `match` e `$`, um "\n" no fim passaria.
    return METRIC_REGEX.fullmatch(value) is not None
