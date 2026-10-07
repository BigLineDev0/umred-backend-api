"""
Règles de validation partagées (source de vérité côté backend).

Factorisées ici une seule fois, puis réutilisées par les serializers
(équipement, laboratoire, consommable, maintenance, profil...). Chaque
fonction NORMALISE d'abord la valeur (trim + espaces multiples réduits à
un seul), puis lève une `serializers.ValidationError` au message court et
en français — ce qui donne une erreur 400 par champ, jamais une 500.

Principe assumé : on bloque ce qui est manifestement invalide (symboles
seuls, HTML, répétitions), on ne prétend pas juger le « sens » d'un nom.
"""
import re
import unicodedata

from rest_framework import serializers

# Caractères de ponctuation autorisés dans un nom commun, en plus des
# lettres (Unicode, accents compris) et des chiffres.
PONCTUATION_NOM = " -'.’/()&+,"
# Une même lettre/symbole répété au moins 4 fois de suite (« aaaa », « ---- »).
_REPETITION = re.compile(r"(.)\1{3,}")
# Balise HTML ou entité (« <b> », « &amp; » suffit à trahir du collage HTML).
_HTML = re.compile(r"<[^>]*>|&#?\w+;")
# Référence : 3 à 40 caractères A-Z 0-9 - _ . / , sans espace, bornes alphanum.
_REFERENCE = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{1,38}[A-Z0-9]$")
# Téléphone sénégalais : +221 puis 9 chiffres commençant par 7.
_TELEPHONE_SN = re.compile(r"^\+2217\d{8}$")


def normaliser_espaces(valeur: str | None) -> str:
    """« Labo   Bio  » -> « Labo Bio ». None/non-str -> chaîne vide."""
    if not isinstance(valeur, str):
        return ""
    return re.sub(r"\s+", " ", valeur).strip()


def _sans_accents(valeur: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", valeur) if unicodedata.category(c) != "Mn")


def cle_unicite(valeur: str | None) -> str:
    """
    Forme canonique pour comparer deux noms : sans accents, insensible à la
    casse, espaces normalisés. « Labo Bio », « labo  bio » et « LABO BIÓ »
    donnent la même clé.
    """
    return _sans_accents(normaliser_espaces(valeur)).casefold()


def valider_nom_commun(valeur: str | None, *, min_len: int = 2, max_len: int = 100) -> str:
    """Nom d'équipement, de laboratoire, de consommable, de personne, localisation."""
    valeur = normaliser_espaces(valeur)
    if _HTML.search(valeur):
        raise serializers.ValidationError("Le texte ne doit pas contenir de balises HTML.")
    if not min_len <= len(valeur) <= max_len:
        raise serializers.ValidationError(f"Doit comporter entre {min_len} et {max_len} caractères.")
    for caractere in valeur:
        if not (caractere.isalnum() or caractere in PONCTUATION_NOM):
            raise serializers.ValidationError(
                "Caractères non autorisés. Utilisez des lettres, des chiffres, l'espace et - ' . / ( ) & + ,"
            )
    if not any(c.isalpha() for c in valeur):
        raise serializers.ValidationError("Doit contenir au moins une lettre.")
    if _REPETITION.search(valeur):
        raise serializers.ValidationError("Évitez la répétition d'un même caractère.")
    return valeur


def valider_reference(valeur: str | None, *, obligatoire: bool = False) -> str:
    """Référence de consommable : normalisée et mise en MAJUSCULES."""
    valeur = normaliser_espaces(valeur).upper()
    if not valeur:
        if obligatoire:
            raise serializers.ValidationError("La référence est obligatoire.")
        return ""
    if not _REFERENCE.match(valeur):
        raise serializers.ValidationError(
            "Référence invalide : 3 à 40 caractères parmi A-Z, 0-9, - _ . / (sans espace)."
        )
    return valeur


def valider_texte_long(valeur: str | None, *, max_len: int = 2000, min_len: int = 0, obligatoire: bool = False) -> str:
    """Description, motif, notes, rapport : HTML interdit, bornes de longueur."""
    valeur = (valeur or "").strip()
    if not valeur:
        if obligatoire:
            raise serializers.ValidationError("Ce champ est obligatoire.")
        return ""
    if _HTML.search(valeur):
        raise serializers.ValidationError("Le texte ne doit pas contenir de balises HTML.")
    if len(valeur) < min_len:
        raise serializers.ValidationError(f"Doit comporter au moins {min_len} caractères.")
    if len(valeur) > max_len:
        raise serializers.ValidationError(f"Ne doit pas dépasser {max_len} caractères.")
    return valeur


def valider_telephone_senegal(valeur: str | None, *, obligatoire: bool = False) -> str:
    """Accepte « 77 123 45 67 » ou « +221771234567 » -> « +221771234567 »."""
    brut = re.sub(r"[\s.\-]", "", valeur or "")
    if not brut:
        if obligatoire:
            raise serializers.ValidationError("Le téléphone est obligatoire.")
        return ""
    if re.fullmatch(r"7\d{8}", brut):
        brut = "+221" + brut
    if not _TELEPHONE_SN.match(brut):
        raise serializers.ValidationError("Téléphone invalide. Format attendu : +221 7X XXX XX XX.")
    return brut
