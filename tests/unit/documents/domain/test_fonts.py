"""Polices d'un modèle : lire le nom, comparer les métriques, choisir une remplaçante, la licence."""

from __future__ import annotations

import pytest

from karria.modules.documents.domain.fonts import (
    FRENCH_CHARSET,
    FontClass,
    FontMetrics,
    FontOrigin,
    FontResolution,
    LibraryFamily,
    SourceFont,
    choose_substitute,
    classify,
    family_from_name,
    family_key,
    italic_from_name,
    licence_allows_reuse,
    metric_distance,
    missing_characters,
    nearest_weight,
    weight_from_name,
)

pytestmark = pytest.mark.unit


class TestNoms:
    @pytest.mark.parametrize(
        ("name", "weight"),
        [
            ("Raleway-SemiBold", 600),
            ("Raleway-ExtraLight", 200),
            ("Raleway-Bold", 700),
            ("Inter Black", 900),
            ("Lato-Regular", 400),
            ("Lato-Thin", 100),
        ],
    )
    def test_la_graisse_la_plus_specifique_du_nom_gagne(self, name: str, weight: int) -> None:
        assert weight_from_name(name) == weight

    def test_un_nom_sans_graisse_donne_la_graisse_par_defaut(self) -> None:
        assert weight_from_name("Raleway") == 400
        assert weight_from_name("Raleway", default=500) == 500

    @pytest.mark.parametrize(
        ("name", "italic"),
        [
            ("Lato-Italic", True),
            ("Helvetica-Oblique", True),
            ("Garamond-It", True),
            ("Garamond_it", True),
            ("Lato-Regular", False),
        ],
    )
    def test_l_italique_se_lit_dans_le_nom(self, name: str, italic: bool) -> None:
        assert italic_from_name(name) is italic

    @pytest.mark.parametrize(
        ("name", "family"),
        [
            ("ABCDEF+OpenSans-SemiBoldItalic", "Open Sans"),
            ("Raleway-Bold", "Raleway"),
            ("RalewayBold", "Raleway"),
            ("ArialMT", "Arial"),
            ("Montserrat,Bold", "Montserrat"),
            ("Bold", "Bold"),
        ],
    )
    def test_la_famille_perd_prefixe_graisse_et_style(self, name: str, family: str) -> None:
        assert family_from_name(name) == family

    def test_la_cle_de_famille_ignore_accents_casse_et_blancs(self) -> None:
        assert family_key("ABCDEF+ÉquitéSans-Bold") == family_key("equite sans") == "equitesans"


class TestGraisseLaPlusProche:
    def test_sans_graisse_disponible_la_demande_est_rendue(self) -> None:
        assert nearest_weight(600, ()) == 600

    def test_la_plus_proche_est_choisie(self) -> None:
        assert nearest_weight(300, (400, 700)) == 400

    def test_a_egalite_au_dela_de_500_la_plus_grasse_gagne(self) -> None:
        assert nearest_weight(600, (500, 700)) == 700

    def test_a_egalite_jusqu_a_500_la_plus_maigre_gagne(self) -> None:
        assert nearest_weight(500, (400, 600)) == 400


WIDTHS = dict.fromkeys("abcde", 0.5)


class TestDistanceDeMetriques:
    def test_deux_polices_identiques_sont_a_distance_nulle(self) -> None:
        m = FontMetrics(x_height=0.5, cap_height=0.7, avg_width=0.5, widths=WIDTHS)
        assert metric_distance(m, m) == 0.0

    def test_la_chasse_se_compare_sur_les_caracteres_communs(self) -> None:
        source = FontMetrics(widths=WIDTHS, avg_width=0.1)
        candidate = FontMetrics(widths=dict.fromkeys("abcdef", 0.6), avg_width=0.9)
        assert metric_distance(source, candidate) == 0.2

    def test_une_chasse_minuscule_est_bornee_a_0_05_em(self) -> None:
        source = FontMetrics(widths=dict.fromkeys("abcde", 0.0))
        candidate = FontMetrics(widths=dict.fromkeys("abcde", 0.05))
        assert metric_distance(source, candidate) == 1.0

    def test_moins_de_cinq_caracteres_communs_retombe_sur_la_chasse_moyenne(self) -> None:
        source = FontMetrics(widths={"a": 0.5}, avg_width=0.5)
        candidate = FontMetrics(widths={"a": 0.9}, avg_width=0.6)
        assert metric_distance(source, candidate) == 0.2

    def test_les_metriques_sont_ponderees_chasse_hauteur_d_x_capitales(self) -> None:
        source = FontMetrics(x_height=0.5, cap_height=0.7, avg_width=0.5)
        candidate = FontMetrics(x_height=0.55, cap_height=0.77, avg_width=0.5)
        assert metric_distance(source, candidate) == round((0.3 * 0.1 + 0.2 * 0.1) / 1.0, 4)

    def test_une_metrique_absente_d_un_cote_ne_compte_pas(self) -> None:
        source = FontMetrics(x_height=0.5, cap_height=0.7)
        candidate = FontMetrics(x_height=0.6)
        assert metric_distance(source, candidate) == 0.2

    def test_sans_metrique_commune_la_distance_est_maximale(self) -> None:
        assert metric_distance(FontMetrics(), FontMetrics(x_height=0.5)) == 1.0


def _family(name: str, cls: FontClass, x: float, *, italics: bool = True) -> LibraryFamily:
    return LibraryFamily(
        family=name,
        font_class=cls,
        licence="OFL",
        metrics=FontMetrics(x_height=x),
        italics=italics,
    )


def _source(cls: FontClass | None, *, italic: bool = False) -> SourceFont:
    return SourceFont(
        name="ABCDEF+Mystery-Regular",
        family="Mystery",
        weight=400,
        italic=italic,
        font_class=cls,
        metrics=FontMetrics(x_height=0.5),
    )


LIBRARY = [
    _family("Lato", FontClass.SANS, 0.6),
    _family("Inter", FontClass.SANS, 0.52, italics=False),
    _family("Lora", FontClass.SERIF, 0.5),
]


class TestRemplacante:
    def test_la_classe_prime_sur_la_ressemblance(self) -> None:
        best, distance = choose_substitute(_source(FontClass.SANS), LIBRARY)  # type: ignore[misc]
        assert best.family == "Inter"
        assert distance == 0.04

    def test_sans_classe_connue_toutes_les_familles_concourent(self) -> None:
        best, distance = choose_substitute(_source(None), LIBRARY)  # type: ignore[misc]
        assert (best.family, distance) == ("Lora", 0.0)

    def test_un_italique_exige_une_famille_qui_en_a_un(self) -> None:
        best, _ = choose_substitute(_source(FontClass.SANS, italic=True), LIBRARY)  # type: ignore[misc]
        assert best.family == "Lato"

    def test_sans_famille_italique_la_classe_suffit(self) -> None:
        library = [_family("Inter", FontClass.SANS, 0.52, italics=False)]
        best, _ = choose_substitute(_source(FontClass.SANS, italic=True), library)  # type: ignore[misc]
        assert best.family == "Inter"

    def test_sans_famille_de_la_classe_toute_la_bibliotheque_concourt(self) -> None:
        best, _ = choose_substitute(_source(FontClass.MONO), LIBRARY)  # type: ignore[misc]
        assert best.family == "Lora"

    def test_a_distance_egale_l_ordre_alphabetique_departage(self) -> None:
        library = [_family("Zed", FontClass.SANS, 0.5), _family("Abel", FontClass.SANS, 0.5)]
        best, _ = choose_substitute(_source(FontClass.SANS), library)  # type: ignore[misc]
        assert best.family == "Abel"

    def test_une_bibliotheque_vide_ne_propose_rien(self) -> None:
        assert choose_substitute(_source(FontClass.SANS), []) is None


class TestCouverture:
    def test_une_police_complete_ne_manque_de_rien(self) -> None:
        assert missing_characters(set(FRENCH_CHARSET)) == frozenset()

    def test_un_sous_ensemble_manque_les_accents_mais_pas_l_espace(self) -> None:
        missing = missing_characters(frozenset("abc"))
        assert "é" in missing
        assert "Œ" in missing
        assert "a" not in missing
        assert " " not in missing


class TestLicence:
    @pytest.mark.parametrize(
        "text",
        [
            "Licensed under the SIL Open Font License, Version 1.1",
            "Apache License, Version 2.0",
            "This font is in the public domain",
            "CC0",
        ],
    )
    def test_une_licence_libre_suffit(self, text: str) -> None:
        assert licence_allows_reuse(text, fs_type=0x0002) is True

    def test_sans_licence_ni_drapeau_la_reutilisation_est_refusee(self) -> None:
        assert licence_allows_reuse("", None) is False
        assert licence_allows_reuse(None, None) is False  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("fs_type", "allowed"),
        [
            (0x0000, True),
            (0x0008, True),
            (0x0002, False),
            (0x0004, False),
            (0x000C, False),
            (0x0100, False),
        ],
    )
    def test_le_drapeau_fstype_decide_sans_licence_libre(self, fs_type: int, allowed: bool) -> None:
        assert licence_allows_reuse("© Foundry, all rights reserved", fs_type) is allowed


class TestClasse:
    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"monospaced": True, "family_class": 8 << 8}, FontClass.MONO),
            ({"family_class": 8 << 8}, FontClass.SANS),
            ({"family_class": 1 << 8}, FontClass.SERIF),
            ({"family_class": 7 << 8}, FontClass.SERIF),
            ({"family_class": 10 << 8}, FontClass.HANDWRITING),
            ({"family_class": 9 << 8}, FontClass.DISPLAY),
            ({"family_class": 12 << 8}, FontClass.DISPLAY),
            ({"panose_serif": 11}, FontClass.SANS),
            ({"panose_serif": 13}, FontClass.SANS),
            ({"panose_serif": 2}, FontClass.SERIF),
            ({"panose_serif": 10}, FontClass.SERIF),
            ({"panose_serif": 0, "name": "Mystery"}, None),
            ({"family_class": 11 << 8, "name": "Roboto Mono"}, FontClass.MONO),
            ({"name": "Source Code Pro"}, FontClass.MONO),
            ({"name": "Space Grotesk"}, FontClass.SANS),
            ({"name": "Noto Sans"}, FontClass.SANS),
            ({"name": "EB Garamond"}, FontClass.SERIF),
            ({"name": "Times New Roman"}, FontClass.SERIF),
            ({"name": "Dancing Script"}, FontClass.HANDWRITING),
            ({"name": "Mystery"}, None),
        ],
    )
    def test_les_tables_puis_le_nom_decident_de_la_classe(
        self, kwargs: dict[str, object], expected: FontClass | None
    ) -> None:
        args: dict[str, object] = {
            "panose_serif": None,
            "family_class": None,
            "monospaced": False,
            "name": "",
        }
        args.update(kwargs)
        assert classify(**args) is expected  # type: ignore[arg-type]

    def test_la_classe_sfamilyclass_prime_sur_la_panose(self) -> None:
        got = classify(panose_serif=11, family_class=3 << 8, monospaced=False, name="Sans")
        assert got is FontClass.SERIF


class TestResolution:
    def test_seule_une_autre_famille_est_une_substitution(self) -> None:
        def res(origin: FontOrigin) -> FontResolution:
            return FontResolution("Src", "Lato", 400, False, origin, "OFL")

        assert res(FontOrigin.SUBSTITUTE).substituted is True
        assert res(FontOrigin.LIBRARY).substituted is False
        assert res(FontOrigin.EMBEDDED).substituted is False


def test_roman_fait_partie_du_nom_de_famille() -> None:
    assert family_from_name("Times New Roman") == "Times New Roman"
    assert family_from_name("ABCDEF+TimesNewRomanPSMT") == "Times New Roman"
