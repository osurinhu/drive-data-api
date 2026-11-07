"""Camada de domínio, em duas partes.

* **O ORM** (``base.py`` e ``models.py``): as entidades persistidas e a classe base.
* **O vocabulário** (todo o resto: ``authz``, ``vin``, ``ports``...): módulos folha,
  que não importam nada do projeto além de outras folhas daqui, nem infraestrutura.
  Qualquer camada e qualquer processo pode importá-los.

Este arquivo fica sem import: quem usa só o vocabulário (o consumer, por exemplo)
não pode carregar o SQLAlchemy por tabela. ``tests/unit/test_fronteiras.py``
guarda as duas regras.
"""
