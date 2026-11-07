"""Camada de regra de negócio.

Serviços decidem o que pode e o que não pode, validam invariantes e orquestram
repositórios. Nunca montam SQL (isso é do repositório) nem lidam com HTTP (isso
é da rota): comunicam problemas levantando as exceções de ``service.errors``,
que o handler central traduz em código HTTP.
"""
