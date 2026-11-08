"""Recebimento dos lotes de telemetria do app (o producer do pipeline)."""

from dependency_injector.wiring import Provide, inject
from flask.views import MethodView
from flask_jwt_extended import current_user
from flask_smorest import Blueprint

from blueprint.http_errors import error_responses
from domain.authz import Permission
from ext.auth import permission_required
from ext.container import Container
from instrumentation.request_context import current_request_id
from schemas.telemetry_schema import BatchReceiptSchema, TelemetryBatchSchema
from service.telemetry.ingest_service import TelemetryIngestService

blp = Blueprint(
    "telemetry",
    __name__,
    url_prefix="/telemetry",
    description="Envio das leituras dos veículos, a caminho da camada bronze.",
)


@blp.route("/batches")
class TelemetryBatches(MethodView):
    # O piso é a própria permissão de envio: não há leitura de telemetria nesta API.
    decorators = [permission_required(Permission.TELEMETRIA_ENVIAR)]

    @inject
    def __init__(
        self,
        ingest_service: TelemetryIngestService = Provide[Container.telemetry_ingest_service],
    ) -> None:
        self.ingest_service = ingest_service

    @permission_required(Permission.TELEMETRIA_ENVIAR)
    @blp.arguments(TelemetryBatchSchema)
    @blp.response(202, BatchReceiptSchema)
    @error_responses(blp, 403, 413, 503)
    def post(self, payload: dict):
        """Recebe um lote de leituras e o enfileira para a camada bronze.

        Formato inválido reprova o lote inteiro (422), e o corpo acima de 1 MiB, com 413.
        A posse é conferida registro a registro: só segue para a fila o registro de
        veículo do qual o usuário autenticado é gestor ou motorista. Os outros voltam em
        `rejected`, e o lote é aceito em parte. O 202 só sai depois que a fila confirmou o
        recebimento; com a fila fora, a resposta é 503 com `Retry-After`, e o lote pode ser
        reenviado inteiro (cada registro é gravado uma vez só, pelo `id`).
        """
        # current_user, e não current_owner_scope(...): ninguém envia em nome de outro.
        return self.ingest_service.ingest(
            sender_id=current_user.id,
            sender_name=current_user.name,
            records=payload["records"],
            request_id=current_request_id(),
        )
