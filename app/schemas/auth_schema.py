"""Schemas de autenticação e identidade."""

from marshmallow import Schema, fields, validate

from domain.authz import Role, permissions_of

#: Os limites do login acompanham a coluna `users.name`, e os da senha, o
#: `UserService`, que os confere de novo para quem não passa por schema (a CLI).
LOGIN = validate.Length(min=1, max=50)


class LoginSchema(Schema):
    name = fields.Str(required=True, validate=LOGIN)
    password = fields.Str(required=True, load_only=True, validate=validate.Length(min=1, max=128))


class RegistrationSchema(Schema):
    name = fields.Str(
        required=True, validate=LOGIN, metadata={"description": "Nome de login, único."}
    )
    password = fields.Str(
        required=True,
        load_only=True,
        validate=validate.Length(min=8, max=128),
        metadata={"description": "Senha, de 8 a 128 caracteres."},
    )


class TokenPairSchema(Schema):
    access_token = fields.Str(metadata={"description": "Token JWT de acesso."})
    refresh_token = fields.Str(
        metadata={"description": "Token JWT de renovação. Cada um vale para uma única renovação."}
    )
    token_type = fields.Str(metadata={"description": "Esquema a usar no header Authorization."})
    expires_in = fields.Int(metadata={"description": "Validade do token de acesso, em segundos."})
    refresh_expires_in = fields.Int(
        metadata={"description": "Validade do token de renovação, em segundos."}
    )


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
