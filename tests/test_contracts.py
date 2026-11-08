"""Contratos internos que nenhum teste funcional pegaria.

Estes testes existem para transformar duplicação de conhecimento em falha de
CI. Cada um guarda um ponto onde a mesma informação vive em dois arquivos e
poderia divergir silenciosamente.
"""

import re
from dataclasses import fields

from blueprint.http_errors import ERROR_STATUS, STATUS_DESCRIPTION, error_response
from domain.authz import ROLE_PERMISSIONS, Permission, Role
from domain.ports import BronzeEntry
from ext.api import api
from instrumentation.correlation import bind_correlation, clear_correlation
from instrumentation.probes import registered_probes
from schemas.bronze_schema import BronzePageSchema, BronzeRecordSchema
from schemas.error_schema import ApiErrorSchema
from schemas.health_schema import HealthSchema
from schemas.pagination_schema import PageSchema
from schemas.telemetry_schema import BatchReceiptSchema, RejectedRecordSchema
from service.errors import ServiceError
from service.pagination import CursorPage, Page
from service.telemetry.ingest_rules import RejectedRecord
from service.telemetry.ingest_service import IngestReceipt


def _concrete_subclasses(cls: type) -> set[type]:
    encontradas = set()
    for sub in cls.__subclasses__():
        encontradas.add(sub)
        encontradas |= _concrete_subclasses(sub)
    return encontradas


def test_toda_excecao_de_dominio_tem_status_http():
    """Falhou? Você criou uma exceção em `service/errors.py` e esqueceu de
    mapeá-la em `blueprint.http_errors.ERROR_STATUS`. Sem o mapa ela cairia no handler
    genérico e viraria um 500 — com este teste, vira erro de CI."""
    nao_mapeadas = _concrete_subclasses(ServiceError) - set(ERROR_STATUS)

    assert not nao_mapeadas, sorted(c.__name__ for c in nao_mapeadas)


def test_todo_status_mapeado_tem_descricao_padrao():
    """`STATUS_DESCRIPTION` alimenta a mensagem padrão e a documentação OpenAPI."""
    sem_descricao = set(ERROR_STATUS.values()) - set(STATUS_DESCRIPTION)

    assert not sem_descricao, sorted(sem_descricao)


def test_envelope_de_paginacao_do_servico_e_do_schema_nao_divergem():
    """Falhou? `service.pagination.Page` e `schemas.PageSchema` descreveram o
    mesmo envelope de formas diferentes. Renomear só um lado faria o campo
    desaparecer da resposta sem nenhum erro."""
    campos_do_servico = {f.name for f in fields(Page)}
    # PageSchema traz só os metadados; `items` é declarado nas subclasses
    # concretas (ex. VehiclePageSchema), porque o tipo do item varia.
    campos_do_schema = set(PageSchema().fields) | {"items"}

    assert campos_do_servico == campos_do_schema


def test_recibo_do_lote_do_servico_e_do_schema_nao_divergem():
    """O par do teste acima, para o 202 do envio de telemetria: o serviço devolve
    `IngestReceipt` (com `RejectedRecord`) e o schema os serializa. Campo
    renomeado de um lado só sumiria do recibo que o app lê."""
    assert {f.name for f in fields(IngestReceipt)} == set(BatchReceiptSchema().fields)
    assert {f.name for f in fields(RejectedRecord)} == set(RejectedRecordSchema().fields)


#: Os campos do `BronzeEntry` que a silver não vê, de propósito: o `available_metrics`
#: existe para o índice da Fase 4, e o bruto já traz as leituras.
OMITIDOS_DA_SILVER = {"available_metrics"}


def test_pagina_do_bronze_do_servico_e_do_schema_nao_divergem():
    """O par para a leitura da silver: o serviço devolve `CursorPage` com itens
    `BronzeEntry`, e o schema os serializa. Um campo renomeado só no envelope sumiria
    da resposta; um do registro que não exista no `BronzeEntry` sairia sempre vazio.
    Um campo novo do `BronzeEntry` reprova até alguém decidir: vai para o schema, ou
    para `OMITIDOS_DA_SILVER`."""
    registro = set(BronzeRecordSchema().fields)
    entrada = {f.name for f in fields(BronzeEntry)}

    assert {f.name for f in fields(CursorPage)} == set(BronzePageSchema().fields)
    assert registro <= entrada
    assert entrada - registro == OMITIDOS_DA_SILVER


def test_envelope_de_erro_do_codigo_e_do_schema_nao_divergem(app):
    """O par do teste acima, para o envelope de erro: `blueprint.http_errors` monta e
    `ApiErrorSchema` documenta, e é o schema que o cliente lê no OpenAPI."""
    with app.test_request_context():
        bind_correlation(request_id="um-id-qualquer")
        resposta, _ = error_response(404, "mensagem")
        clear_correlation()
        chaves = set(resposta.get_json())

    # `errors` só aparece nos 422 que o próprio flask-smorest monta.
    assert chaves | {"errors"} == set(ApiErrorSchema().fields)


def test_toda_sonda_registrada_tem_campo_no_schema_do_health(app):
    """Falhou? Uma extensão registrou uma sonda (`register_probe`) cujo nome não
    é campo do `HealthSchema`. O schema é a única fonte da saída: o health
    executaria a sonda e a esconderia da resposta, sem erro nenhum. O perfil da
    suíte carrega todas as extensões, então todas as sondas estão aqui."""
    sem_campo = set(registered_probes(app)) - set(HealthSchema().fields)

    assert not sem_campo, sorted(sem_campo)


# --- Autorização --------------------------------------------------------------

#: As únicas operações que dispensam token. Abrir uma rota é um ato
#: deliberado: acrescente-a aqui, e só então os testes abaixo voltam a passar.
ROTAS_PUBLICAS = {
    ("GET", "/api/v1/health/"),
    ("GET", "/api/v1/health/live"),
    ("POST", "/api/v1/auth/token"),
    ("POST", "/api/v1/auth/register"),
}

#: As únicas operações que aceitam o token de renovação no lugar do de acesso.
ROTAS_DE_RENOVACAO = {
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
}


def test_todo_papel_tem_entrada_em_role_permissions():
    """Falhou? Você acrescentou um papel em `app/domain/authz.py` e não disse o que ele
    pode. `permissions_of` devolveria conjunto vazio e o papel novo não
    conseguiria fazer nada — sem nenhum erro que apontasse para cá."""
    sem_permissoes = set(Role) - set(ROLE_PERMISSIONS)

    assert not sem_permissoes, sorted(p.value for p in sem_permissoes)


def test_toda_permissao_e_concedida_a_algum_papel():
    """Falhou? Você declarou uma permissão e esqueceu de concedê-la. A rota que
    a exigir fica inacessível para todo mundo, e nada no código indica isso."""
    concedidas = set().union(*ROLE_PERMISSIONS.values())
    orfas = set(Permission) - concedidas

    assert not orfas, sorted(p.value for p in orfas)


def test_so_as_rotas_previstas_sao_publicas():
    """Falhou? Uma operação passou a declarar `@blp.doc(security=PUBLIC)`, ou
    deixou de declarar. O spec é a fonte certa para perguntar: é por ele que a
    exigência global de token é anunciada, e `security: []` é a única forma de
    uma rota sair dela."""
    spec = api.spec.to_dict()
    publicas = {
        (verbo.upper(), caminho)
        for caminho, operacoes in spec.get("paths", {}).items()
        for verbo, operacao in operacoes.items()
        if isinstance(operacao, dict) and operacao.get("security") == []
    }

    assert publicas == ROTAS_PUBLICAS


def test_cada_operacao_fica_no_grupo_do_seu_blueprint(app):
    """Falhou? As rotas voltaram a ser registradas sob um blueprint pai: o flask-smorest
    agrupa o Swagger pelo blueprint registrado no ``Api``, e tudo cairia no grupo do pai."""
    spec = api.spec.to_dict()
    blueprint_da_rota = {
        re.sub(r"<(?:[^:<>]+:)?([^<>]+)>", r"{\1}", regra.rule): regra.endpoint.split(".")[0]
        for regra in app.url_map.iter_rules()
    }

    fora_do_grupo = [
        f"{verbo.upper()} {caminho}: {operacao['tags']}"
        for caminho, operacoes in spec["paths"].items()
        for verbo, operacao in operacoes.items()
        if isinstance(operacao, dict) and operacao["tags"] != [blueprint_da_rota[caminho]]
    ]

    assert not fora_do_grupo, fora_do_grupo
    grupos = {tag["name"]: tag["description"] for tag in spec["tags"]}
    assert set(grupos) == {blueprint_da_rota[caminho] for caminho in spec["paths"]}
    assert all(grupos.values()), grupos


def test_toda_rota_protegida_declara_a_permissao_que_exige(app):
    """Falhou? Uma operação nova não passou por `permission_required` — nem na
    lista `decorators` da classe, nem no método. Ela estaria apenas
    *autenticada*, aberta a qualquer papel, e nenhum teste funcional notaria.

    Rota que qualquer usuário autenticado pode acessar declara isso com
    `authenticated`, que é `permission_required()` sem argumentos.
    """
    sem_declaracao = []
    for regra in app.url_map.iter_rules():
        classe = getattr(app.view_functions[regra.endpoint], "view_class", None)
        if classe is None:
            continue

        piso_da_classe = any(hasattr(d, "__permissoes__") for d in classe.decorators)
        for verbo in sorted((regra.methods or set()) - {"HEAD", "OPTIONS"}):
            metodo = getattr(classe, verbo.lower(), None)
            if metodo is None or (verbo, regra.rule) in ROTAS_PUBLICAS:
                continue
            if piso_da_classe or hasattr(metodo, "__permissoes__"):
                continue
            sem_declaracao.append(f"{verbo} {regra.rule}")

    assert not sem_declaracao, sorted(sem_declaracao)


VERBOS_DE_ESCRITA = {"POST", "PUT", "PATCH", "DELETE"}


def test_so_renovar_e_encerrar_a_sessao_aceitam_o_token_de_renovacao(app):
    """Falhou? Uma rota nova usa `@refresh_token_required`. O decorator não exige
    permissão nenhuma e o teste seguinte o aceita como declaração: numa rota de
    negócio, ele abriria a escrita a qualquer sessão."""
    encontradas = set()
    for regra in app.url_map.iter_rules():
        classe = getattr(app.view_functions[regra.endpoint], "view_class", None)
        if classe is None:
            continue
        for verbo in regra.methods or set():
            metodo = getattr(classe, verbo.lower(), None)
            if getattr(metodo, "__token_de_renovacao__", False):
                encontradas.add((verbo, regra.rule))

    assert encontradas == ROTAS_DE_RENOVACAO


def test_toda_escrita_protegida_declara_a_propria_permissao(app):
    """Falhou? Um método de escrita ficou só com o piso da classe, que é de
    leitura: quem só lê passaria a escrever. O teste acima aceita o piso como
    declaração, e o 403 de um papel sem leitura nenhuma (como a `silver` no
    veículo) sai do piso, então nenhum dos dois perceberia. Rota de escrita que
    qualquer autenticado pode chamar declara `@authenticated` no método."""
    sem_declaracao = []
    for regra in app.url_map.iter_rules():
        classe = getattr(app.view_functions[regra.endpoint], "view_class", None)
        if classe is None:
            continue
        for verbo in sorted((regra.methods or set()) & VERBOS_DE_ESCRITA):
            metodo = getattr(classe, verbo.lower(), None)
            if metodo is None or (verbo, regra.rule) in ROTAS_PUBLICAS:
                continue
            if not hasattr(metodo, "__permissoes__"):
                sem_declaracao.append(f"{verbo} {regra.rule}")

    assert not sem_declaracao, sorted(sem_declaracao)
