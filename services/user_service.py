from datetime import datetime, time, timezone
import uuid
from models import MessageLog, Utilisateur
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def get_or_create_user(
    platform_id: str,
    session_db: AsyncSession,
    prenom_defaut: str = "Citoyen",
) -> Utilisateur:
    """Récupère l'utilisateur ou le crée silencieusement avec son ID plateforme et son nom par défaut."""
    stmt = select(Utilisateur).where(Utilisateur.whatsapp_id == platform_id)
    result = await session_db.execute(stmt)
    utilisateur = result.scalar_one_or_none()

    if not utilisateur:
        utilisateur = Utilisateur(
            whatsapp_id=platform_id,
            prenom_eleve=prenom_defaut or "Citoyen",
            pays="France",
            dashboard_token=uuid.uuid4().hex,
            etape_onboarding="TERMINE",
        )
        session_db.add(utilisateur)
        await session_db.commit()
        await session_db.refresh(utilisateur)

    return utilisateur


LIMITE_MESSAGES_JOUR = 40


async def verifier_quota_journalier(
    id_utilisateur: int, db: AsyncSession
) -> tuple[bool, int]:
    """Compte les messages envoyés aujourd'hui par l'élève.

    Retourne (est_autorise, nb_messages_aujourdhui).
    """
    debut_journee = datetime.combine(datetime.utcnow().date(), time.min)

    stmt = select(func.count(MessageLog.id)).where(
        MessageLog.id_utilisateur == id_utilisateur,
        MessageLog.role == "user",
        MessageLog.cree_le >= debut_journee,
    )
    result = await db.execute(stmt)
    nb_aujourdhui = result.scalar_one_or_none() or 0

    return (nb_aujourdhui < LIMITE_MESSAGES_JOUR, nb_aujourdhui)