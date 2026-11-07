---
paths:
  - "app/messaging/**/*.py"
---

# Mensageria (`app/messaging/`)

Tudo que fala com o RabbitMQ: o pika e o cliente da API de gerenciamento (stdlib). O lugar na
arquitetura está em 01-arquitetura (tabela da infraestrutura). As decisões estão em
02-decisoes: "pika para o RabbitMQ, PyMongo para o Mongo", "API de gerenciamento do RabbitMQ
com a stdlib" e "A sonda do RabbitMQ abre uma conexão nova a cada chamada".

- Não importa `ext`, `repository`, `service` nem `blueprint`. URL, credencial e prazos chegam
  por parâmetro, de quem monta o objeto (a extensão ou o container). O pika só é importado aqui.
- Os parâmetros de conexão saem de `connection_parameters`: uma tentativa só, os prazos do
  `settings.toml` valendo sobre os da URL e um `connection_name` que diz o processo. Nada monta
  `pika.URLParameters` por conta própria.
- A `BlockingConnection` não é segura entre threads e só atende o heartbeat quando é usada: uma
  por worker, criada depois do fork. Espera longa usa `connection.sleep()`, nunca
  `time.sleep()`, que deixaria o broker derrubar a conexão por falta de heartbeat.
- Log e mensagem de erro nunca repetem a URL, porque ela leva a senha. O erro do pika vai para o
  log por `describe_error`, já que o `AMQPConnectionError` tem `str()` vazio. A URL que não se
  deixa ler vira mensagem fixa com `from None`: o urllib repete trechos dela.
- API de gerenciamento: PUT idempotente; nome de vhost e de usuário passa por `_segment` (o vhost
  padrão se chama `/`); `ManagementApiError` nunca leva a credencial.
- Testes contra o broker de verdade em `tests/rabbitmq/`, no vhost `drive-data-test` (fixture
  `amqp_vhost`); sem broker, só o que não conecta, em `tests/unit/`.
