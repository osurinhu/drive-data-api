"""Peças de observabilidade usadas por mais de uma extensão e pelas rotas.

Pacote neutro: não pode morar em ``ext/``, porque extensão não importa extensão.
Hoje guarda o registro das sondas do health e o id da requisição; os logs estruturados, a correlação
entre requisição e mensagem e as métricas chegam com a Fase 6.
"""
