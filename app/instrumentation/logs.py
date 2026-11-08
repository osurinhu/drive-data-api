"""O formato dos logs: uma linha JSON por evento, com a correlação do momento.

Cada linha traz ``ts``, ``level``, ``logger``, ``message``, ``process`` (o perfil), os
ids de correlação que estiverem valendo e os campos do evento, passados em
``extra={"event": "...", ...}``. Serializar a linha inteira é o que escapa o conteúdo:
um id ou uma mensagem com quebra de linha não forjam uma linha de log.
"""

import json
import logging
from datetime import UTC, datetime

from instrumentation.correlation import current_correlation

TEXT_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_FORMATS = ("json", "text")

#: Os atributos que todo ``LogRecord`` tem: o resto veio do ``extra``.
_STANDARD_ATTRIBUTES = {
    *logging.LogRecord("", 0, "", 0, "", (), None).__dict__,
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def __init__(self, process: str) -> None:
        super().__init__()
        self._process = process

    def format(self, record: logging.LogRecord) -> str:
        moment = datetime.fromtimestamp(record.created, UTC)
        line = {
            "ts": moment.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "process": self._process,
            **current_correlation(),
            **{k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRIBUTES},
        }
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, ensure_ascii=False, default=str)


def configure_logging(level: str, log_format: str, *, process: str) -> None:
    """Instala o formato na raiz dos logs. Levanta ``RuntimeError`` se ele não existir."""
    if log_format not in _FORMATS:
        raise RuntimeError(f"LOG_FORMAT inválido: use um de {', '.join(_FORMATS)}.")
    handler = logging.StreamHandler()
    formatter = JsonFormatter(process) if log_format == "json" else logging.Formatter(TEXT_FORMAT)
    handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=[handler])
