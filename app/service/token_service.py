"""Tokens de renovação: registro, rotação e revogação da família.

Não importa a biblioteca de JWT: a rota emite os tokens, e aqui só se decide pelo
``jti`` (o id do token) o que cada um ainda vale. Quem chega até este serviço já
provou ser o dono do token, pela assinatura: o ``jti`` é a própria credencial, e por
isso não há escopo de dono.

Não estende ``BaseService`` porque não é CRUD.
"""

from datetime import UTC, datetime

from domain.models import RefreshToken
from repository.sql.refresh_token_repository import RefreshTokenRepository
from service.errors import AuthenticationError

#: A mesma para token desconhecido, revogado ou reapresentado: quem o apresenta não
#: precisa saber qual dos casos é.
INVALID_REFRESH_MESSAGE = "Token de renovação inválido."


class TokenService:
    def __init__(self, repository: RefreshTokenRepository) -> None:
        self.repository = repository

    def register(self, user_id: int, jti: str, expires_at: datetime) -> RefreshToken:
        """Registra o token de um login novo, que abre uma família: a dele é o próprio jti."""
        token = self.repository.add(
            RefreshToken(jti=jti, family_id=jti, user_id=user_id, expires_at=expires_at)
        )
        self.repository.commit()
        return token

    def rotate(self, jti: str, *, new_jti: str, expires_at: datetime) -> RefreshToken:
        """Troca um token pelo seguinte da mesma família.

        Token já trocado que volta é indício de roubo, porque só um dos dois lados (o
        dono ou quem o copiou) pode estar com o token certo: a família inteira cai e os
        dois precisam fazer login de novo. O commit vem antes da exceção, senão a
        revogação iria embora com a resposta de erro.
        """
        current = self.repository.get_active_for_update(jti)
        if current is None:
            raise AuthenticationError(INVALID_REFRESH_MESSAGE)
        if current.replaced_by_jti is not None:
            self.repository.revoke_family(current.family_id)
            self.repository.commit()
            raise AuthenticationError(INVALID_REFRESH_MESSAGE)

        current.replaced_by_jti = new_jti
        token = self.repository.add(
            RefreshToken(
                jti=new_jti,
                family_id=current.family_id,
                user_id=current.user_id,
                expires_at=expires_at,
            )
        )
        self.repository.commit()
        return token

    def revoke_family(self, jti: str) -> None:
        """Encerra a sessão a que o token pertence (o logout)."""
        current = self.repository.get_active(jti)
        if current is None:
            raise AuthenticationError(INVALID_REFRESH_MESSAGE)
        self.repository.revoke_family(current.family_id)
        self.repository.commit()

    def prune(self) -> int:
        """Apaga os tokens expirados e devolve quantos eram."""
        removed = self.repository.delete_expired(datetime.now(UTC))
        self.repository.commit()
        return removed
