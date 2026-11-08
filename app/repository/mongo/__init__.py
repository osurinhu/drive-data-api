"""Adaptadores do MongoDB (PyMongo): o bronze da telemetria.

Implementam as portas de ``domain/ports.py`` e pedem o banco à ``ext.mongo``
(``mongo.db``), como os repositórios SQL pedem o ``db`` à ``ext.database``. O
layout do documento mora num lugar só (``bronze_document.py``), e o serviço só
conhece o ``BronzeEntry``.
"""
