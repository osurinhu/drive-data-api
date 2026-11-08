from datetime import datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update

from domain.models import RefreshToken
from ext.database import db
from repository.sql.base_repository import BaseRepository


class RefreshTokenRepository(BaseRepository[RefreshToken]):
    def __init__(self) -> None:
        super().__init__(RefreshToken)

    def get_active(self, jti: str) -> RefreshToken | None:
        return self.get_first_by_filter(jti=jti)

    def get_active_for_update(self, jti: str) -> RefreshToken | None:
        """Carrega o token ativo e trava a linha até o fim da transação: de duas
        renovações simultâneas com o mesmo token, a segunda espera e vê o token já trocado.
        """
        stmt = self._select().filter_by(jti=jti).with_for_update()
        return db.session.execute(stmt).scalar_one_or_none()

    def revoke_family(self, family_id: str) -> None:
        self._revoke(RefreshToken.family_id == family_id)

    def revoke_all_of_user(self, user_id: int) -> None:
        self._revoke(RefreshToken.user_id == user_id)

    def _revoke(self, criterion: Any) -> None:
        """Exclusão lógica em massa, sem commit: quem chama fecha a transação.

        Duas instruções de propósito. O ``UPDATE`` sozinho esperaria uma renovação em
        andamento, mas enxergaria só as linhas da foto dele, e o token novo que ela
        acabou de inserir escaparia da revogação. O ``SELECT ... FOR UPDATE`` faz essa
        espera antes, e o ``UPDATE`` seguinte já nasce vendo o token novo.
        """
        db.session.execute(select(RefreshToken.id).where(criterion).with_for_update())
        db.session.execute(
            update(RefreshToken)
            .where(criterion, RefreshToken.deleted.is_(False))
            .values(deleted=True)
        )

    def delete_expired(self, now: datetime) -> int:
        """Apaga de vez os que já expiraram, revogados ou não: o JWT vencido é recusado
        pela assinatura, e a linha não serve para mais nada."""
        result = db.session.execute(delete(RefreshToken).where(RefreshToken.expires_at < now))
        return cast("CursorResult[Any]", result).rowcount
