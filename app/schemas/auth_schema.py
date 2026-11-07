"""Schemas de autenticação e identidade."""

from marshmallow import Schema, fields, validate

from authz import Role, permissions_of


class LoginSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=50))
    password = fields.Str(required=True, load_only=True, validate=validate.Length(min=1))


class TokenSchema(Schema):
    access_token = fields.Str(metadata={"description": "Token JWT de acesso."})
    token_type = fields.Str(metadata={"description": "Esquema a usar no header Authorization."})
    expires_in = fields.Int(metadata={"description": "Validade do token, em segundos."})


class UserSchema(Schema):
    """Saída pública de um usuário — nunca expõe o hash da senha."""

    id = fields.Int()
    name = fields.Str()
    role = fields.Enum(Role, by_value=fields.Str, metadata={"description": "Papel do usuário."})
    created_at = fields.DateTime()

    # Derivado do papel. Sem isto, todo cliente reimplementaria o mapa
    # papel -> permissões para decidir qual botão mostrar — uma segunda fonte de
    # verdade, e justamente a que ninguém lembra de atualizar.
    permissions = fields.Method(
        "_permissions",
        metadata={"description": "Permissões concedidas pelo papel."},
    )

    def _permissions(self, user) -> list[str]:
        return sorted(permission.value for permission in permissions_of(user.role))
