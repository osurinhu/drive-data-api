"""O health contra as dependências de verdade, sem dublê em sonda nenhuma."""

HEALTH_URL = "/api/v1/health/"


def test_o_health_sonda_de_verdade_as_tres_dependencias(client):
    """Cada sonda conecta no serviço do compose: o Postgres responde ao ``SELECT 1``,
    o Mongo ao ``ping`` e o RabbitMQ aceita a credencial no vhost de testes."""
    response = client.get(HEALTH_URL)

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "database": "ok",
        "mongo": "ok",
        "rabbitmq": "ok",
    }
