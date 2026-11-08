"""Vocabulário dos instantes que vão ao bronze: a data que cabe em UTC.

Folha do domínio: não importa nada do projeto. O bronze guarda as datas em UTC, e nos extremos
do calendário o fuso leva a data para fora do que o Python representa: ela passaria pelo schema
e quebraria ao virar BSON. O envio recusa a data de coleta, e a leitura da silver, os filtros.
"""

from datetime import UTC, datetime

#: A regra em palavras, para quem recusa uma data dizer o porquê.
INVALID_MOMENT_MESSAGE = "Data fora do intervalo aceito."


def fits_in_utc(moment: datetime) -> bool:
    try:
        moment.astimezone(UTC)
    except OverflowError:
        return False
    return True
