"""
Rapport d'activité PDF, généré côté serveur à partir des données (et non
une impression de la page web) : mise en page A4 stable, en-tête aux
couleurs et au logo de l'établissement, tableaux paginés.
"""
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

STATUTS = {
    'EN_ATTENTE': 'En attente', 'VALIDEE': 'Validée', 'REFUSEE': 'Refusée',
    'ANNULEE': 'Annulée', 'TERMINEE': 'Terminée',
}
NIVEAUX = {'critique': 'Critique', 'attention': 'Attention', 'info': 'Info'}
MAX_LIGNES_DETAIL = 300


def _p(texte, style):
    # Paragraph interprète un mini-HTML : on échappe les données saisies
    # par les utilisateurs (noms, motifs) pour ne pas casser la mise en page.
    from xml.sax.saxutils import escape
    return Paragraph(escape(str(texte)), style)


def generer_rapport_pdf(indicateurs, reservations, organisation=None, laboratoire_nom=None):
    tampon = BytesIO()
    doc = SimpleDocTemplate(
        tampon, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        title="Rapport d'activité", author=organisation.nom if organisation else 'UMRED Labo',
    )
    couleur = colors.HexColor(organisation.couleur_primaire if organisation else '#1848D9')
    styles = getSampleStyleSheet()
    titre = ParagraphStyle('titre', parent=styles['Title'], alignment=TA_LEFT, textColor=couleur, fontSize=18)
    h2 = ParagraphStyle('h2', parent=styles['Heading2'], textColor=couleur, spaceBefore=12, spaceAfter=6)
    normal = ParagraphStyle('normal', parent=styles['BodyText'], fontSize=9, leading=12)
    petit = ParagraphStyle('petit', parent=normal, fontSize=8, leading=10)

    def tableau(donnees, largeurs, entete=True):
        t = Table(donnees, colWidths=largeurs, repeatRows=1 if entete else 0)
        style = [
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#CBD5E1')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F1F5F9')]),
            ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]
        if entete:
            style += [('BACKGROUND', (0, 0), (-1, 0), couleur), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                      ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold')]
        t.setStyle(TableStyle(style))
        return t

    elements = []

    # --- En-tête ---
    entete = []
    if organisation and organisation.logo:
        try:
            entete.append(Image(organisation.logo.path, width=2.2 * cm, height=2.2 * cm, kind='proportional'))
        except Exception:
            pass  # logo illisible : le rapport reste générable sans lui
    periode = indicateurs['periode']
    bloc_titre = [
        Paragraph("Rapport d'activité", titre),
        _p(organisation.nom if organisation else 'UMRED Labo', normal),
        _p(f"Période du {periode['debut']} au {periode['fin']}"
           + (f" — {laboratoire_nom}" if laboratoire_nom else ''), normal),
        _p(f"Généré le {timezone.localtime():%d/%m/%Y à %H:%M}", petit),
    ]
    if entete:
        t = Table([[entete[0], bloc_titre]], colWidths=[2.8 * cm, None])
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE')]))
        elements.append(t)
    else:
        elements.extend(bloc_titre)
    elements.append(Spacer(1, 0.4 * cm))

    # --- Indicateurs clés ---
    v = indicateurs['volumes']
    elements.append(Paragraph('Indicateurs clés', h2))
    kpis = [
        ['Réservations', 'Validées', 'En attente', 'Heures utilisées', 'Utilisateurs actifs'],
        [v['reservations'], v['validees'], v['en_attente'], v['heures_utilisation'], v['utilisateurs_actifs']],
        ["Taux d'annulation", 'Taux de refus', 'Délai moyen de validation', 'En attente > 48 h', ''],
        [f"{v['taux_annulation']} %", f"{v['taux_refus']} %",
         f"{v['delai_moyen_validation_h']} h" if v['delai_moyen_validation_h'] is not None else '—',
         v['demandes_en_attente_48h'], ''],
    ]
    t = tableau(kpis, [3.5 * cm] * 5, entete=False)
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTNAME', (0, 2), (-1, 2), 'Helvetica-Bold'),
        ('TEXTCOLOR', (0, 0), (-1, 0), couleur), ('TEXTCOLOR', (0, 2), (-1, 2), couleur),
        ('FONTSIZE', (0, 1), (-1, 1), 12), ('FONTSIZE', (0, 3), (-1, 3), 12),
    ]))
    elements.append(t)

    # --- Recommandations ---
    if indicateurs['recommandations']:
        elements.append(Paragraph('Recommandations', h2))
        lignes = [['Niveau', 'Constat', 'Recommandation']]
        for r in indicateurs['recommandations']:
            lignes.append([NIVEAUX[r['niveau']], _p(r['titre'], petit), _p(r['message'], petit)])
        elements.append(tableau(lignes, [2 * cm, 5 * cm, 10.8 * cm]))

    # --- Occupation ---
    elements.append(Paragraph('Occupation des laboratoires', h2))
    lignes = [['Laboratoire', 'Heures réservées', "Taux d'occupation"]]
    lignes += [[_p(l['nom'], petit), l['heures'], f"{l['taux_occupation']} %"] for l in indicateurs['occupation_laboratoires']]
    elements.append(tableau(lignes, [10 * cm, 3.9 * cm, 3.9 * cm]))

    elements.append(Paragraph('Équipements les plus utilisés', h2))
    lignes = [['Équipement', 'Laboratoire', 'Heures', 'Occupation']]
    lignes += [[_p(e['nom'], petit), _p(e['laboratoire'], petit), e['heures'], f"{e['taux_occupation']} %"]
               for e in indicateurs['occupation_equipements'][:15]]
    elements.append(tableau(lignes, [6.5 * cm, 6 * cm, 2.6 * cm, 2.7 * cm]))

    # --- Prévision ---
    elements.append(Paragraph('Prévision de charge — semaine prochaine', h2))
    lignes = [['Jour', 'Date', 'Réservations attendues', 'Déjà planifiées']]
    lignes += [[j['jour'], j['date'], j['attendues'], j['deja_planifiees']] for j in indicateurs['prevision_semaine']]
    elements.append(tableau(lignes, [3.5 * cm, 3.5 * cm, 5.4 * cm, 5.4 * cm]))

    # --- Détail ---
    elements.append(Paragraph('Détail des réservations', h2))
    lignes = [['Date', 'Horaire', 'Laboratoire', 'Équipement(s)', 'Demandeur', 'Statut']]
    for r in reservations[:MAX_LIGNES_DETAIL]:
        equipements = ', '.join(e.nom for e in r.equipements.all()) or 'Salle'
        lignes.append([
            f'{r.date:%d/%m/%Y}', f'{r.heure_debut:%H:%M}-{r.heure_fin:%H:%M}', _p(r.laboratoire.nom, petit),
            _p(equipements, petit), _p(r.demandeur.nom_complet, petit), STATUTS.get(r.statut, r.statut),
        ])
    elements.append(tableau(lignes, [2 * cm, 2.2 * cm, 3.6 * cm, 4.2 * cm, 3.6 * cm, 2.2 * cm]))
    if len(reservations) > MAX_LIGNES_DETAIL:
        elements.append(_p(f"… {len(reservations) - MAX_LIGNES_DETAIL} réservations supplémentaires : "
                           "utilisez l'export Excel pour le détail complet.", petit))

    def pied_de_page(canvas, document):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#64748B'))
        canvas.drawString(1.6 * cm, 0.9 * cm, f"{organisation.nom if organisation else 'UMRED Labo'} — rapport d'activité")
        canvas.drawRightString(A4[0] - 1.6 * cm, 0.9 * cm, f'Page {document.page}')
        canvas.restoreState()

    doc.build(elements, onFirstPage=pied_de_page, onLaterPages=pied_de_page)
    return tampon.getvalue()
