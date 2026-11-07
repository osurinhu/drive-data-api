"""Camada de acesso a dados, com uma subpasta por tecnologia.

``sql/`` guarda os repositórios SQLAlchemy, a única parte do projeto que lê e grava dados
com SQL (a sonda do health, em ``ext/database.py``, só confere a conexão). Os serviços pedem
operações e os repositórios as traduzem para o armazenamento, o que deixa a regra de negócio
independente da tecnologia de persistência.

Os ``__init__.py`` deste pacote só têm docstring: um re-export faria quem importa uma
tecnologia carregar também a outra.
"""
