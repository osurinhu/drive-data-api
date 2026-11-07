"""Envelope de erro, para documentar as respostas de falha no OpenAPI.

Precisa espelhar exatamente o que ``http_errors.error_response`` monta.

O nome da classe é ``ApiErrorSchema`` (e não ``ErrorSchema``) de propósito: o
apispec deriva o nome do componente removendo o sufixo "Schema", e "Error" já é
usado pelo schema interno do flask-smorest — a colisão gera aviso e renomeia o
componente automaticamente.
"""

from marshmallow import Schema, fields


class ApiErrorSchema(Schema):
    code = fields.Int(metadata={"description": "Código HTTP."})
    status = fields.Str(metadata={"description": "Nome do status HTTP."})
    message = fields.Str(metadata={"description": "Descrição legível do erro."})
    request_id = fields.Str(
        metadata={
            "description": (
                "Id desta requisição, também devolvido no header X-Request-ID. "
                "Informe-o ao relatar o problema: é por ele que se acha o log."
            )
        }
    )
    errors = fields.Dict(
        metadata={"description": "Detalhamento por campo, quando houver erro de validação."}
    )
