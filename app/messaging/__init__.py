"""Adaptadores do RabbitMQ: todo acesso ao pika e à API de gerenciamento passa por aqui.

Os módulos deste pacote recebem URL e prazos por parâmetro e nunca importam
``ext``, ``repository`` nem ``blueprint``. Quem os conecta à aplicação é o
container (os adaptadores) e ``ext.rabbitmq`` (a sonda e os comandos).
"""
