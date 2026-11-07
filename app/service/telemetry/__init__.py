"""O pipeline de telemetria: regras que dependem só de portas e de folhas do domínio.

Sem ``BaseService`` e sem infraestrutura: nada aqui importa ``flask``, ``pika``,
``pymongo`` ou ``sqlalchemy``. Os adaptadores chegam pelo construtor (o container
os escolhe), e cada regra se testa com dublês em memória. O nome do módulo diz o
lado: ``ingest_*`` é do producer. Este arquivo fica sem import, para que um lado
não carregue o outro.
"""
