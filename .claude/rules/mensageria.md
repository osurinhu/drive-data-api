---
paths:
  - "app/messaging/**/*.py"
---

# Mensageria (`app/messaging/`)

Tudo que fala com o RabbitMQ: o pika e o cliente da API de gerenciamento (stdlib). O lugar na
arquitetura está em 01-arquitetura (tabela da infraestrutura). As decisões estão em
02-decisoes: "pika para o RabbitMQ, PyMongo para o Mongo", "API de gerenciamento do RabbitMQ
com a stdlib", "A sonda do RabbitMQ abre uma conexão nova a cada chamada" e, no pipeline,
"O 202 só sai depois da confirmação do broker", "A topologia é aplicada pelo job `setup`", "O
destino de cada mensagem" e "Parada e queda".

- Do projeto, só importa as folhas do domínio (o contrato e as portas): nada de `ext`,
  `repository`, `service` nem `blueprint`. URL, credencial e prazos chegam por parâmetro, de quem
  monta o objeto (a extensão ou o container). O pika só é importado aqui.
- Os parâmetros de conexão saem de `connection_parameters`: uma tentativa só, os prazos do
  `settings.toml` valendo sobre os da URL e um `connection_name` que diz o processo. Nada monta
  `pika.URLParameters` por conta própria.
- A `BlockingConnection` não é segura entre threads e só atende o heartbeat quando é usada: uma
  por processo, criada depois do fork (o `_pid` a descarta no filho), e um `threading.Lock` em
  volta do uso. Espera longa usa `connection.sleep()`, nunca `time.sleep()`, que deixaria o
  broker derrubar a conexão por falta de heartbeat.
- Publicador (`publisher.py`): canal com `confirm_delivery()`, `mandatory=True` e mensagem
  persistente; exchange, routing key e corpo vêm do contrato (`encode`), nunca escritos aqui.
  `NackError` e `UnroutableError` não se repetem (o broker respondeu). Erro de conexão se repete
  **uma** vez, e só na conexão reaproveitada; a recém-aberta falha direto. Toda falha sai como
  `PublishFailed` (a porta), nunca como exceção do pika.
- Consumer (`consumer.py`): ack manual e `basic_qos(prefetch)`. Decodifica pelo contrato e
  entrega o lote ao handler, que chega pelo construtor (o serviço); não conhece o Mongo. O
  destino segue a tabela de 02-decisoes e não muda sem rever a contagem do `delivery-limit`:
  - ack só depois de o handler voltar sem erro;
  - `ContractError` → `basic_reject(requeue=False)` (a DLQ);
  - `BronzeUnavailable` → `basic_nack(requeue=True)` e a pausa. O `nack` não conta para o
    `delivery-limit` (RabbitMQ 4.3+); o `reject` com requeue e a queda da conexão com mensagem
    pendente contam;
  - outra exceção → `basic_reject(requeue=True)`, que conta: passado o limite, a DLQ.
  Nunca feche o canal ou a conexão para "devolver" mensagens numa falha transitória: conta como
  falha.
- O `stop()` só levanta uma marca (é chamado de handler de sinal). O laço roda
  `process_data_events` em fatias de 1 s, a pausa usa `connection.sleep` em fatias de 1 s, e as
  duas olham a marca. Falha do broker sai como `ConsumerFailed`, nunca como exceção do pika.
- Topologia: declarada só em `topology.py` (`apply`), e aplicada pelo `flask rabbitmq setup` e
  pela fixture `amqp_topology`. Producer e consumer nunca declaram exchange nem fila. Argumento
  de fila é imutável: o que pode mudar (DLX, limites) vai pela policy `bronze-telemetry`. O
  `dead-letter-strategy: at-least-once` exige `overflow: reject-publish`; sem ele, o RabbitMQ
  volta ao at-most-once calado.
- Log e mensagem de erro nunca repetem a URL, porque ela leva a senha. O erro do pika vai para o
  log por `describe_error`, já que o `AMQPConnectionError` tem `str()` vazio. A URL que não se
  deixa ler vira mensagem fixa com `from None`: o urllib repete trechos dela.
- API de gerenciamento: pedidos idempotentes por `_send` (PUT para vhost, permissão, exchange,
  fila e policy; POST para o binding, que o RabbitMQ não duplica). Nome de vhost, de usuário, de exchange e
  de fila passa por `_segment` (o vhost padrão se chama `/`). O `ManagementApiError` traz o
  começo da resposta (é ali que vem o "inequivalent arg") e nunca a credencial.
- Testes contra o broker de verdade em `tests/rabbitmq/`, no vhost `drive-data-test` (fixtures
  `amqp_vhost`, `amqp_topology`, `bronze_queue`, `dead_letters` e `publicar`); sem broker, só o
  que não conecta, em `tests/unit/`. O consumer se testa com `run(idle_timeout=...)`, que drena
  a fila e volta, e um handler de teste no lugar do serviço (`tests/rabbitmq/test_consumo.py`). A contagem de mensagens de fila quorum no `rabbitmqctl list_queues` atrasa
  alguns segundos: quem confere por ela (o smoke) espera; os testes drenam a fila com
  `basic_get`.
