"""Sondas de disponibilidade: prontidão (``/health/``) e vida (``/health/live``).

``/health/`` é o alvo do healthcheck do container, do readiness probe e do load
balancer. Não basta o processo estar vivo: ele verifica de verdade cada
dependência que este processo usa, pelas sondas que as extensões registraram.
Só a queda de uma dependência crítica (``HEALTH_CRITICAL``, por perfil)
responde 503.

``/health/live`` não consulta nada. É o liveness probe, que reinicia o processo
quando falha, e uma dependência fora não volta reiniciando quem depende dela.
"""

from flask import current_app
from flask.views import MethodView
from flask_smorest import Blueprint

from ext.api import PUBLIC
from instrumentation.probes import registered_probes
from schemas.health_schema import HealthSchema, LivenessSchema

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
    @blp.alt_response(503, schema=HealthSchema, description="Uma dependência crítica está fora")
    def get(self):
        """Verifica cada dependência que este processo usa.

        Responde 503 só quando cai uma dependência crítica do processo. As outras
        aparecem como `unreachable`, com `status` `degraded` e 200.
        """
        estados = {
            nome: "ok" if sonda() else "unreachable"
            for nome, sonda in registered_probes(current_app).items()
        }
        fora = {nome for nome, estado in estados.items() if estado != "ok"}
        criticas = set(current_app.config["HEALTH_CRITICAL"])

        corpo = {"status": "degraded" if fora else "ok", **estados}
        return corpo, 503 if fora & criticas else 200


@blp.route("/live")
class Liveness(MethodView):
    @blp.doc(security=PUBLIC)
    @blp.response(200, LivenessSchema)
    def get(self):
        """Confirma que o processo está no ar, sem consultar dependência nenhuma."""
        return {"status": "ok"}
