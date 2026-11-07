"""Sonda de disponibilidade.

Alvo do healthcheck do container, do readiness probe e do load balancer. Não
basta responder 200 pelo processo estar vivo: um processo no ar sem banco não
serve requisição nenhuma, então a sonda verifica a conexão de verdade.
"""

from flask import current_app
from flask.views import MethodView
from flask_smorest import Blueprint
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ext.api import PUBLIC
from ext.database import db
from schemas.health_schema import HealthSchema

blp = Blueprint(
    "health",
    __name__,
    url_prefix="/health",
    description="Disponibilidade da aplicação e das suas dependências",
)


@blp.route("/")
class Health(MethodView):
    @blp.doc(security=PUBLIC)
    @blp.response(200, HealthSchema)
    @blp.alt_response(503, schema=HealthSchema, description="Alguma dependência está fora")
    def get(self):
        """Verifica a aplicação e a conexão com o banco."""
        try:
            db.session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            # Sessão fica inutilizável após falha — reverter antes de devolver.
            db.session.rollback()
            current_app.logger.exception("Health check: banco inacessível")
            return {"status": "degraded", "database": "unreachable"}, 503

        return {"status": "ok", "database": "ok"}
