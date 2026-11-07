"""Cliente mínimo da API HTTP de gerenciamento do RabbitMQ, só com a stdlib.

É por ela que vhosts e permissões são aplicados (e, com a topologia, exchanges,
filas e policy). A API é idempotente, porque o PUT cria ou mantém, e usa as
credenciais de operação, que os processos que só publicam ou só consomem nunca
recebem.
"""

import base64
import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, unquote, urlsplit
from urllib.request import Request, urlopen


class ManagementApiError(RuntimeError):
    """A API de gerenciamento recusou o pedido ou não respondeu."""


class ManagementApi:
    """Pedidos à API de gerenciamento, autenticados pela credencial da URL de operação."""

    def __init__(self, url: str, *, timeout: float) -> None:
        partes = urlsplit(url)
        if partes.scheme not in ("http", "https") or not partes.hostname:
            raise ValueError(
                "a URL da API de gerenciamento não é http(s)://usuario:senha@host:porta"
            )

        endereco = partes.netloc.rpartition("@")[2]
        self._base = f"{partes.scheme}://{endereco}{partes.path.rstrip('/')}"
        credencial = f"{unquote(partes.username or '')}:{unquote(partes.password or '')}"
        self._authorization = "Basic " + base64.b64encode(credencial.encode()).decode()
        self._timeout = timeout

    def ensure_vhost(self, vhost: str) -> None:
        """Cria o vhost, se ainda não existir."""
        self._put(f"/api/vhosts/{_segment(vhost)}", {})

    def grant(self, vhost: str, user: str, *, configure: str, write: str, read: str) -> None:
        """Define as permissões do usuário no vhost.

        Cada permissão é uma expressão regular sobre o nome do recurso (exchange
        ou fila): ``".*"`` libera todos, ``"^$"`` não libera nenhum.
        """
        self._put(
            f"/api/permissions/{_segment(vhost)}/{_segment(user)}",
            {"configure": configure, "write": write, "read": read},
        )

    def _put(self, path: str, body: dict[str, Any]) -> None:
        request = Request(
            self._base + path,
            data=json.dumps(body).encode(),
            method="PUT",
            headers={"Authorization": self._authorization, "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self._timeout):
                pass
        except HTTPError as error:
            raise ManagementApiError(f"PUT {path} recusado: {error.code} {error.reason}") from error
        except OSError as error:
            # URLError é um OSError; nenhum dos dois repete a credencial na mensagem.
            raise ManagementApiError(f"API de gerenciamento inacessível ({error})") from error


def _segment(name: str) -> str:
    # O vhost padrão do RabbitMQ se chama "/": vira %2F para caber num segmento da URL.
    return quote(name, safe="")
