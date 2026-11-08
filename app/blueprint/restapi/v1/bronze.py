"""Leitura incremental do bronze pela camada silver (o processo bronze-api)."""

from dependency_injector.wiring import Provide, inject
from flask.views import MethodView
from flask_smorest import Blueprint

from blueprint.http_errors import error_responses
from domain.authz import Permission
from ext.auth import current_owner_scope, permission_required
from ext.container import Container
from schemas.bronze_schema import BronzePageSchema, BronzeQuerySchema
from service.telemetry.bronze_read_service import BronzeReadService

blp = Blueprint(
    "bronze",
    __name__,
    url_prefix="/bronze",
    description="Leitura da camada bronze pela camada silver, em ordem de gravação, por cursor.",
)


@blp.route("/records")
class BronzeRecords(MethodView):
    decorators = [permission_required(Permission.BRONZE_LER)]

    @inject
    def __init__(
        self, read_service: BronzeReadService = Provide[Container.bronze_read_service]
    ) -> None:
        self.read_service = read_service

    @blp.arguments(BronzeQuerySchema, location="query")
    @blp.response(200, BronzePageSchema)
    @error_responses(blp, 403, 503)
    def get(self, query: dict):
        """Lê os registros do bronze depois do cursor, na ordem em que foram gravados.

        Para ler tudo sem perder nem repetir: chame com o cursor guardado (sem ele, a
        leitura começa do primeiro registro), processe a página e só então guarde o
        `next_cursor`. Com `has_more`, chame de novo em seguida; sem ele, espere o
        intervalo de polling. Os registros gravados nos últimos segundos ficam para
        as próximas chamadas (`watermark`). Uma queda entre processar e guardar repete
        a página: deduplique por `record_id`. Com o bronze fora, a resposta é 503, e a
        chamada pode ser repetida com o mesmo cursor.

        O cursor vale para os filtros com que foi emitido: para mudar de filtro, comece
        de novo, sem cursor (ou com `ingested_from`).
        """
        return self.read_service.read(
            current_owner_scope(Permission.BRONZE_LER_DE_TODOS),
            after=query["after"],
            ingested_from=query["ingested_from"],
            filters=query["filters"],
            limit=query["limit"],
        )
