"""Repositórios SQLAlchemy: a única parte do projeto que monta SQL.

Cada repositório concreto é uma subclasse de ``BaseRepository`` que amarra o modelo. Uma
consulta que o CRUD genérico não cobre entra como método da subclasse, nunca no serviço.
"""
