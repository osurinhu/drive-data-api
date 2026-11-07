"""Contratos internos que nenhum teste funcional pegaria.

Estes testes existem para transformar duplicação de conhecimento em falha de
CI. Cada um guarda um ponto onde a mesma informação vive em dois arquivos e
poderia divergir silenciosamente.
"""

from dataclasses import fields

from flask import g

from authz import ROLE_PERMISSIONS, Permission, Role
from ext.api import api
from http_errors import ERROR_STATUS, STATUS_DESCRIPTION, error_response
from instrumentation.probes import registered_probes
from schemas.error_schema import ApiErrorSchema
from schemas.health_schema import HealthSchema
from schemas.pagination_schema import PageSchema
from service.errors import ServiceError
from service.pagination import Page


def _concrete_subclasses(cls: type) -> set[type]:
    encontradas = set()
    for sub in cls.__subclasses__():
        encontradas.add(sub)
        encontradas |= _concrete_subclasses(sub)
    return encontradas


def test_toda_excecao_de_dominio_tem_status_http():
    """Falhou? Você criou uma exceção em `service/errors.py` e esqueceu de
    mapeá-la em `http_errors.ERROR_STATUS`. Sem o mapa ela cairia no handler
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
    # concretas (ex. ItemPageSchema), porque o tipo do item varia.
    campos_do_schema = set(PageSchema().fields) | {"items"}

    assert campos_do_servico == campos_do_schema


def test_envelope_de_erro_do_codigo_e_do_schema_nao_divergem(app):
    """O par do teste acima, para o envelope de erro: `http_errors` monta e
    `ApiErrorSchema` documenta, e é o schema que o cliente lê no OpenAPI."""
    with app.test_request_context():
        g.request_id = "um-id-qualquer"
        resposta, _ = error_response(404, "mensagem")
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
}


def test_todo_papel_tem_entrada_em_role_permissions():
    """Falhou? Você acrescentou um papel em `app/authz.py` e não disse o que ele
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
