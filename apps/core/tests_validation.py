"""Tests des règles de validation partagées (apps/core/validation.py)."""
from django.test import SimpleTestCase
from rest_framework import serializers

from apps.core import validation as v


class NomCommunTests(SimpleTestCase):
    def test_normalise_les_espaces(self):
        self.assertEqual(v.valider_nom_commun("  Microscope   optique "), "Microscope optique")

    def test_accents_et_ponctuation_autorises(self):
        for nom in ["Spectrophotomètre UV", "Centrifugeuse (5810 R)", "Pince & étau", "Bécher 0.5/1L"]:
            self.assertEqual(v.valider_nom_commun(nom), nom)

    def test_valeurs_invalides(self):
        for mauvais in ["", "a", "123", "----", "@@@", "aaaa", "<b>x</b>", "x" * 101]:
            with self.assertRaises(serializers.ValidationError, msg=mauvais):
                v.valider_nom_commun(mauvais)

    def test_bornes(self):
        self.assertEqual(v.valider_nom_commun("ab"), "ab")           # min
        long = ("Microscope " * 10).strip()[:100]  # 100 car. sans répétition excessive
        self.assertEqual(len(v.valider_nom_commun(long)), 100)  # max


class ReferenceTests(SimpleTestCase):
    def test_mise_en_majuscules(self):
        self.assertEqual(v.valider_reference(" ref-001 "), "REF-001")

    def test_formats_acceptes(self):
        for ref in ["REF-001", "ETH/2024", "ABC.12", "NACL-500ML"]:
            self.assertEqual(v.valider_reference(ref), ref)

    def test_formats_refuses(self):
        for mauvais in ["--", "ab", "a b", "re<f>", "x" * 41]:
            with self.assertRaises(serializers.ValidationError, msg=mauvais):
                v.valider_reference(mauvais)

    def test_vide_optionnel_ou_obligatoire(self):
        self.assertEqual(v.valider_reference("", obligatoire=False), "")
        with self.assertRaises(serializers.ValidationError):
            v.valider_reference("", obligatoire=True)


class TelephoneTests(SimpleTestCase):
    def test_formats_acceptes(self):
        self.assertEqual(v.valider_telephone_senegal("77 123 45 67"), "+221771234567")
        self.assertEqual(v.valider_telephone_senegal("+221781234567"), "+221781234567")

    def test_formats_refuses(self):
        for mauvais in ["+221611234567", "70123", "+33123456789", "771234"]:
            with self.assertRaises(serializers.ValidationError, msg=mauvais):
                v.valider_telephone_senegal(mauvais)

    def test_optionnel(self):
        self.assertEqual(v.valider_telephone_senegal(""), "")


class TexteLongTests(SimpleTestCase):
    def test_html_refuse(self):
        with self.assertRaises(serializers.ValidationError):
            v.valider_texte_long("<script>alert(1)</script>")

    def test_min_et_obligatoire(self):
        with self.assertRaises(serializers.ValidationError):
            v.valider_texte_long("   ", obligatoire=True)
        with self.assertRaises(serializers.ValidationError):
            v.valider_texte_long("court", min_len=10)

    def test_max(self):
        with self.assertRaises(serializers.ValidationError):
            v.valider_texte_long("x" * 2001, max_len=2000)


class CleUniciteTests(SimpleTestCase):
    def test_insensible_casse_accents_espaces(self):
        self.assertEqual(v.cle_unicite("Labo  Bió"), v.cle_unicite("labo bio"))
        self.assertNotEqual(v.cle_unicite("Labo Bio"), v.cle_unicite("Labo Chimie"))
