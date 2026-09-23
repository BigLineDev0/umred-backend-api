from dataclasses import dataclass


@dataclass
class AlerteUsure:
    niveau: str  # 'info', 'attention', 'critique'
    message: str
    heures_cumulees: float
    seuil: int
    pannes_recentes: int


def evaluer_usure(equipement) -> AlerteUsure | None:
    """
    Algorithme à seuils, déterministe et explicable : pas de modèle
    entraîné, juste deux règles métier combinées. Choix assumé pour la
    V1 — un vrai modèle prédictif (par exemple une régression sur
    l'historique de pannes) serait l'évolution naturelle si assez de
    données étaient accumulées sur plusieurs années d'usage réel.
    """
    heures = equipement.heures_utilisation_depuis_derniere_maintenance()
    seuil = equipement.seuil_heures_maintenance
    pannes = equipement.pannes_signalees_recentes()

    ratio_usure = heures / seuil if seuil else 0

    if pannes >= 2:
        return AlerteUsure(
            niveau='critique',
            message=f"{pannes} pannes signalées sur les 90 derniers jours, une maintenance préventive est fortement recommandée avant la prochaine panne.",
            heures_cumulees=heures, seuil=seuil, pannes_recentes=pannes,
        )
    if ratio_usure >= 1.0:
        return AlerteUsure(
            niveau='critique',
            message=f"{heures} heures d'utilisation depuis la dernière maintenance, le seuil de {seuil}h est dépassé.",
            heures_cumulees=heures, seuil=seuil, pannes_recentes=pannes,
        )
    if ratio_usure >= 0.8 or pannes == 1:
        return AlerteUsure(
            niveau='attention',
            message=f"{heures} heures d'utilisation sur {seuil}h avant maintenance recommandée, planifiez une intervention prochainement.",
            heures_cumulees=heures, seuil=seuil, pannes_recentes=pannes,
        )
    return None