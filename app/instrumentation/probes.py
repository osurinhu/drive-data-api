"""Registro das sondas de disponibilidade das dependências externas.

Cada extensão que conecta um serviço registra aqui a sua sonda, e a rota de
health executa as que estiverem registradas. Assim a rota não importa a extensão
de serviço externo nenhum nem precisa saber o que o perfil do processo carregou:
cada processo informa exatamente as dependências que usa.
"""

from collections.abc import Callable

from flask import Flask

#: Responde se a dependência atende agora. Não levanta exceção: a falha é a
#: resposta (``False``), e quem a registra no log é a própria sonda.
Probe = Callable[[], bool]

_EXTENSION_KEY = "health_probes"


def register_probe(app: Flask, name: str, probe: Probe) -> None:
    """Registra a sonda de ``name``, que vira a chave da dependência no health."""
    probes: dict[str, Probe] = app.extensions.setdefault(_EXTENSION_KEY, {})
    if name in probes:
        raise RuntimeError(f"A sonda '{name}' foi registrada duas vezes.")
    probes[name] = probe


def registered_probes(app: Flask) -> dict[str, Probe]:
    """As sondas do processo, na ordem em que as extensões as registraram."""
    return dict(app.extensions.get(_EXTENSION_KEY, {}))
