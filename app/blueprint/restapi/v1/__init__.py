"""Versão 1 da API.

Cada módulo expõe um Blueprint chamado ``blp``, registrado por
``ext.config.load_routes`` a partir da lista ``API_V1_ROUTES`` do settings.toml.

é também onde o container aplica o *wiring*: os marcadores ``Provide[...]`` só
funcionam dentro deste pacote (ver ``ext.container``).
"""
