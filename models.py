import secrets
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import relationship

from database import Base


class Utilisateur(Base):
    __tablename__ = "utilisateurs"

    id_utilisateur = Column(Integer, primary_key=True, index=True)
    whatsapp_id = Column(String(30), unique=True, nullable=False, index=True)

    # Identité & profil
    prenom_eleve = Column(String(50), nullable=True)
    nom_eleve = Column(String(50), nullable=True)
    prenom = Column(String(100), nullable=True)
    nom = Column(String(100), nullable=True)
    genre = Column(String(20), nullable=True)
    classe = Column(String(20), nullable=True)
    pays = Column(String(50), nullable=True, default="France")
    pays_code = Column(String(5), default="BJ")
    telephone_parent = Column(String(30), nullable=True)

    # Statut & engagement
    etape_onboarding = Column(String(30), default="TERMINE")
    opt_in_classement = Column(Boolean, default=False)
    pseudo_honneur = Column(String(40), unique=True, nullable=True)
    streak_jours_consecutifs = Column(Integer, default=0)
    derniere_activite_date = Column(Date, default=func.current_date())

    # Accès & suivi
    type_acces = Column(String(20), default="decouverte")
    date_fin_acces = Column(DateTime(timezone=True), nullable=True)
    dashboard_token = Column(
        String(64), unique=True, nullable=False, default=lambda: secrets.token_hex(16)
    )
    dernier_bilan_hebdo_le = Column(Date, nullable=True)
    bilan_decouverte_envoye = Column(Boolean, default=False)
    cree_le = Column(DateTime(timezone=True), server_default=func.now())

    # Relations
    messages = relationship(
        "MessageLog", back_populates="utilisateur", cascade="all, delete-orphan"
    )


class MessageLog(Base):
    __tablename__ = "messages_logs"

    id = Column(BigInteger, primary_key=True, index=True)
    id_utilisateur = Column(
        BigInteger,
        ForeignKey("utilisateurs.id_utilisateur", ondelete="CASCADE"),
        nullable=True,
    )
    role = Column(String(20), nullable=False)  # 'user' ou 'assistant'
    contenu = Column(Text, nullable=False)
    type_media = Column(String(20), default="texte")
    media_url = Column(Text, nullable=True)
    tokens_entree = Column(Integer, default=0)
    tokens_sortie = Column(Integer, default=0)
    cree_le = Column(DateTime(timezone=True), server_default=func.now())

    # Relations
    utilisateur = relationship("Utilisateur", back_populates="messages")    