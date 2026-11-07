"""Papéis e permissões — o vocabulário da autorização.

Uma **permissão** é uma capacidade (`item:escrever`); um **papel** é um pacote
nomeado de permissões, e é o papel que fica gravado em `users.role`. A rota
declara a permissão que exige, nunca o papel.

Módulo folha: não importa nada do projeto, então qualquer camada pode usá-lo sem
criar ciclo. O raciocínio e as alternativas descartadas estão em
`documentacao/02-decisoes.md`, seção Segurança.
"""

from collections.abc import Mapping
from enum import StrEnum


class Permission(StrEnum):
    # Os escopos de leitura e de escrita são permissões separadas de propósito:
    # é o que permite um papel que vê tudo e altera só o que é dele.
    ITEM_LER = "item:ler"
    ITEM_ESCREVER = "item:escrever"
    ITEM_LER_DE_TODOS = "item:ler-de-todos"
    ITEM_ESCREVER_DE_TODOS = "item:escrever-de-todos"


class Role(StrEnum):
    # O que vai para a coluna é o **valor**, não o nome do membro — ver o
    # `values_callable` em `domain/models.py`.
    LEITOR = "leitor"
    USUARIO = "usuario"
    GERENTE = "gerente"
    ADMIN = "admin"


# Cada papel parte do anterior, para que a hierarquia esteja escrita no código em
# vez de depender de o leitor comparar listas.
_LEITOR = frozenset({Permission.ITEM_LER})
_USUARIO = _LEITOR | {Permission.ITEM_ESCREVER}
_GERENTE = _USUARIO | {Permission.ITEM_LER_DE_TODOS}
_ADMIN = _GERENTE | {Permission.ITEM_ESCREVER_DE_TODOS}

#: Única fonte de verdade da autorização. `tests/test_contracts.py` reprova se um
#: papel ficar sem entrada aqui, ou se uma permissão não for concedida a ninguém.
ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = {
    Role.LEITOR: _LEITOR,
    Role.USUARIO: _USUARIO,
    Role.GERENTE: _GERENTE,
    Role.ADMIN: _ADMIN,
}


def permissions_of(role: Role) -> frozenset[Permission]:
    """Permissões de um papel — vazio, e não `KeyError`, para papel desconhecido:
    um papel sem entrada no mapa deve negar acesso, não virar um 500."""
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: Role, *permissions: Permission) -> bool:
    """Verdadeiro se o papel tiver **todas** as permissões informadas."""
    return permissions_of(role).issuperset(permissions)
