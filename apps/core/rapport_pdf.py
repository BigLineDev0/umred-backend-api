"""
Rapport d'activité PDF, généré côté serveur à partir des données (et non
une impression de la page web) : mise en page A4 stable, en-tête aux
couleurs et au logo de l'établissement, tableaux paginés.
"""
from io import BytesIO
from pathlib import Path

from django.utils import timezone
from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
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

MARQUE = 'SenLab'
# Logo de la plateforme, utilisé quand l'établissement n'a pas le sien.
LOGO_PLATEFORME = Path(__file__).resolve().parent / 'assets' / 'logo-senlab-white.png'
# Au-delà de cette luminance moyenne (0-255), un logo est jugé « clair » :
# posé tel quel sur fond blanc il serait invisible.
SEUIL_LOGO_CLAIR = 170
LOGO_LARGEUR_MAX, LOGO_HAUTEUR_MAX = 3.6 * cm, 1.6 * cm


def _preparer_logo(donnees):
    """
    Renvoie (Image reportlab, est_clair) ou None si le fichier est illisible.
    Le logo est recadré sur sa zone visible (les PNG ont souvent de larges
    marges transparentes qui le rapetissent), puis mis à l'échelle en
    conservant ses proportions. Sa luminance décide du fond : un logo clair
    (blanc sur transparent) se pose sur le bandeau coloré, un logo foncé
    dans une pastille blanche. Ainsi n'importe quel logo reste lisible.
    """
    try:
        img = PILImage.open(BytesIO(donnees))
        img.load()
    except Exception:
        return None
    img = img.convert('RGBA')
    boite = img.getchannel('A').getbbox()
    if boite:
        img = img.crop(boite)
    pixels = [p for p in img.resize((64, max(1, 64 * img.height // max(img.width, 1)))).getdata() if p[3] > 128]
    luminance = sum(0.299 * r + 0.587 * g + 0.114 * b for r, g, b, _ in pixels) / len(pixels) if pixels else 0
    # Un logo opaque (JPEG, fond blanc) a une luminance élevée à cause de
    # son fond : seul un logo à fond transparent peut être « clair ».
    transparent = img.getchannel('A').getextrema()[0] < 255
    est_clair = transparent and luminance > SEUIL_LOGO_CLAIR

    echelle = min(LOGO_LARGEUR_MAX / img.width, LOGO_HAUTEUR_MAX / img.height)
    tampon = BytesIO()
    img.save(tampon, format='PNG')
    tampon.seek(0)
    return Image(tampon, width=img.width * echelle, height=img.height * echelle), est_clair


def _lire_logo(organisation):
    if organisation and organisation.logo:
        try:
            # Lecture par le stockage (et non .path) : fonctionne aussi quand
            # les fichiers sont sur un stockage distant (S3, Supabase...).
            with organisation.logo.open('rb') as fichier:
                logo = _preparer_logo(fichier.read())
            if logo:
                return logo
        except Exception:
            pass  # logo illisible : on se rabat sur celui de la plateforme
    try:
        return _preparer_logo(LOGO_PLATEFORME.read_bytes())
    except OSError:
        return None


def _date_fr(valeur):
    # La période arrive en date ou en chaîne ISO (AAAA-MM-JJ).
    if hasattr(valeur, 'strftime'):
        return f'{valeur:%d/%m/%Y}'
    annee, mois, jour = str(valeur).split('-')
    return f'{jour}/{mois}/{annee}'


def _p(texte, style):
    # Paragraph interprète un mini-HTML : on échappe les données saisies
    # par les utilisateurs (noms, motifs) pour ne pas casser la mise en page.
    from xml.sax.saxutils import escape
    return Paragraph(escape(str(texte)), style)


def generer_rapport_pdf(indicateurs, reservations, organisation=None, laboratoire_nom=None, auteur=None):
    tampon = BytesIO()
    nom_etablissement = organisation.nom if organisation else MARQUE
    doc = SimpleDocTemplate(
        tampon, pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        title=f"Rapport d'activité — {nom_etablissement}", author=nom_etablissement, creator=MARQUE,
    )
    couleur = colors.HexColor(organisation.couleur_primaire if organisation else '#1848D9')
    styles = getSampleStyleSheet()
    titre = ParagraphStyle('titre', parent=styles['Title'], alignment=TA_LEFT, textColor=couleur, fontSize=18)
    # keepWithNext : un titre de section ne reste jamais seul en bas de page.
    h2 = ParagraphStyle('h2', parent=styles['Heading2'], textColor=couleur, spaceBefore=12, spaceAfter=6,
                        keepWithNext=1)
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
    # Bandeau aux couleurs de l'établissement, en trois colonnes alignées
    # verticalement au centre : logo | titre et établissement | période.
    largeur_utile = A4[0] - doc.leftMargin - doc.rightMargin
    blanc = colors.white
    attenue = colors.Color(1, 1, 1, alpha=0.85)
    st_titre = ParagraphStyle('entete_titre', fontName='Helvetica-Bold', fontSize=17, leading=20, textColor=blanc)
    st_sous = ParagraphStyle('entete_sous', fontName='Helvetica', fontSize=10, leading=13, textColor=attenue)
    st_meta = ParagraphStyle('entete_meta', fontName='Helvetica', fontSize=8.5, leading=12, textColor=attenue,
                             alignment=TA_RIGHT)
    st_meta_fort = ParagraphStyle('entete_meta_fort', parent=st_meta, fontName='Helvetica-Bold', textColor=blanc)

    periode = indicateurs['periode']
    debut = _date_fr(periode['debut'])
    fin = _date_fr(periode['fin'])
    bloc_titre = [
        Paragraph("Rapport d'activité", st_titre),
        _p(nom_etablissement + (f" · {laboratoire_nom}" if laboratoire_nom else ''), st_sous),
    ]
    bloc_meta = [
        _p(f"Du {debut} au {fin}", st_meta_fort),
        _p(f"Généré le {timezone.localtime():%d/%m/%Y à %H:%M}", st_meta),
    ]
    if auteur:
        bloc_meta.append(_p(f"par {auteur.nom_complet}", st_meta))

    logo = _lire_logo(organisation)
    cellule_logo = ''
    if logo:
        image, est_clair = logo
        if est_clair:
            cellule_logo = image
        else:
            # Pastille blanche derrière un logo foncé ou opaque.
            cellule_logo = Table([[image]], cornerRadii=[4, 4, 4, 4], style=TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), blanc),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
    largeur_logo = LOGO_LARGEUR_MAX + 0.8 * cm if logo else 0.01 * cm
    largeur_meta = 5.2 * cm
    bandeau = Table(
        [[cellule_logo, bloc_titre, bloc_meta]],
        colWidths=[largeur_logo, largeur_utile - largeur_logo - largeur_meta, largeur_meta],
        cornerRadii=[6, 6, 6, 6],
    )
    bandeau.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), couleur),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, 0), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 12), ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 12),
    ]))
    elements.append(bandeau)
    elements.append(Spacer(1, 0.5 * cm))

    # --- Indicateurs clés ---
    v = indicateurs['volumes']
    elements.append(Paragraph('Indicateurs clés', h2))
    libelle = ParagraphStyle('kpi_libelle', parent=petit, fontName='Helvetica-Bold', textColor=couleur)
    kpis = [
        [_p(x, libelle) for x in ['Réservations', 'Validées', 'En attente', 'Heures utilisées', 'Utilisateurs actifs']],
        [v['reservations'], v['validees'], v['en_attente'], v['heures_utilisation'], v['utilisateurs_actifs']],
        [_p(x, libelle) for x in ["Taux d'annulation", 'Taux de refus', 'Délai moyen de validation', 'En attente > 48 h', '']],
        [f"{v['taux_annulation']} %", f"{v['taux_refus']} %",
         f"{v['delai_moyen_validation_h']} h" if v['delai_moyen_validation_h'] is not None else '—',
         v['demandes_en_attente_48h'], ''],
    ]
    t = tableau(kpis, [(A4[0] - doc.leftMargin - doc.rightMargin) / 5] * 5, entete=False)
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
    lignes += [[j['jour'], _date_fr(j['date']), j['attendues'], j['deja_planifiees']] for j in indicateurs['prevision_semaine']]
    elements.append(tableau(lignes, [3.5 * cm, 3.5 * cm, 5.4 * cm, 5.4 * cm]))

    # --- Détail ---
    elements.append(Paragraph('Détail des réservations', h2))
    # « Traité par » : traçabilité de la décision (validation ou refus).
    lignes = [['Date', 'Horaire', 'Laboratoire', 'Équipement(s)', 'Demandeur', 'Statut', 'Traité par']]
    for r in reservations[:MAX_LIGNES_DETAIL]:
        equipements = ', '.join(e.nom for e in r.equipements.all()) or 'Salle'
        lignes.append([
            f'{r.date:%d/%m/%Y}', f'{r.heure_debut:%H:%M}-{r.heure_fin:%H:%M}', _p(r.laboratoire.nom, petit),
            _p(equipements, petit), _p(r.demandeur.nom_complet, petit), STATUTS.get(r.statut, r.statut),
            _p(r.validateur.nom_complet if r.validateur
               else 'Automatique' if r.statut in ('VALIDEE', 'TERMINEE') else '—', petit),
        ])
    elements.append(tableau(lignes, [1.8 * cm, 2 * cm, 3 * cm, 3.4 * cm, 2.9 * cm, 1.8 * cm, 2.9 * cm]))
    if len(reservations) > MAX_LIGNES_DETAIL:
        elements.append(_p(f"… {len(reservations) - MAX_LIGNES_DETAIL} réservations supplémentaires : "
                           "utilisez l'export Excel pour le détail complet.", petit))

    def pied_de_page(canvas, document):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#64748B'))
        canvas.drawString(1.6 * cm, 0.9 * cm, f"{nom_etablissement} — rapport d'activité · généré avec {MARQUE}")
        canvas.drawRightString(A4[0] - 1.6 * cm, 0.9 * cm, f'Page {document.page}')
        canvas.restoreState()

    doc.build(elements, onFirstPage=pied_de_page, onLaterPages=pied_de_page)
    return tampon.getvalue()
