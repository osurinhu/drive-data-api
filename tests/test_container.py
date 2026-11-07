"""Substituição de dependências pelo container.

A razão de existir do `dependency-injector` neste projeto é poder trocar uma
camada por um dublê sem gambiarra. Este teste prova que isso funciona de fato
— e serve de molde para testar uma rota sem tocar no banco.
"""

from dependency_injector import providers

from service.pagination import Page

ITEM_URL = "/api/v1/item/"


class FakeItemService:
    """Dublê: não conhece banco nenhum."""

    def get_page(self, _owner_scope, **_kwargs) -> Page:
        return Page(
            items=[],
            current_page=1,
            total_of_items=99,
            total_of_pages=1,
            has_prev=False,
            has_next=False,
        )


def test_container_permite_substituir_o_servico_por_um_duble(app, client, headers):
    with app.container.item_service.override(providers.Object(FakeItemService())):
        body = client.get(ITEM_URL, headers=headers).get_json()

    assert body["total_of_items"] == 99


def test_substituicao_e_desfeita_ao_sair_do_contexto(app, client, headers):
    with app.container.item_service.override(providers.Object(FakeItemService())):
        pass

    body = client.get(ITEM_URL, headers=headers).get_json()

    assert body["total_of_items"] == 0
