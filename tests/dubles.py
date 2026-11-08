"""Dublês que os testes de mais de uma pasta importam."""


class ObservadorEmMemoria:
    """Serve no lugar de qualquer porta de observação: guarda cada chamada como
    ``(nome, kwargs)``, com os argumentos posicionais em ``args``."""

    def __init__(self) -> None:
        self.chamadas: list[tuple[str, dict]] = []

    def __getattr__(self, nome: str):
        def _registrar(*args, **kwargs):
            self.chamadas.append((nome, {"args": args, **kwargs}))

        return _registrar

    def nomes(self) -> list[str]:
        return [nome for nome, _ in self.chamadas]

    def de(self, nome: str) -> list[dict]:
        return [kwargs for chamada, kwargs in self.chamadas if chamada == nome]
