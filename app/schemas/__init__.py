"""Schemas marshmallow: o contrato de entrada e saída da API.

São a única fonte de verdade sobre quais campos entram e quais saem — por isso
os modelos ORM não têm mixin de serialização. O flask-smorest usa estes schemas
tanto para validar requisições quanto para gerar a documentação OpenAPI.
"""
