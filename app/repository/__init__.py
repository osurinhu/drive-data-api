"""Camada de acesso a dados.

Única camada que monta consultas SQLAlchemy. Serviços pedem operações; os
repositórios traduzem para SQL. Isso mantém a regra de negócio independente da
tecnologia de persistência.
"""
