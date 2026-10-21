"""Normalisation des dates, niveaux et puces : les formes que le contrat impose."""

from __future__ import annotations

import pytest

from karria.modules.cv.domain import (
    LanguageLevel,
    SkillLevel,
    clean_bullets,
    coerce_language_level,
    coerce_skill_level,
    fold,
    normalize_year_month,
)

pytestmark = pytest.mark.unit


class TestDates:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2021-09", "2021-09"),
            ("2021-9", "2021-09"),
            ("2021-09-15", "2021-09"),
            ("2021/09", "2021-09"),
            ("2022-09-01T00:00:00Z", "2022-09"),
            ("09/2021", "2021-09"),
            ("15/09/2021", "2021-09"),
            ("2021", "2021"),
            ("septembre 2021", "2021-09"),
            ("Sept. 2021", "2021-09"),
            ("février 2020", "2020-02"),
            ("Aug 2019", "2019-08"),
            ("  juin 2023 ", "2023-06"),
        ],
    )
    def test_les_formes_courantes_deviennent_aaaa_mm(self, raw: str, expected: str) -> None:
        assert normalize_year_month(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "aujourd'hui", "Présent", "en cours", "now"])
    def test_une_periode_ouverte_vaut_none(self, raw: str | None) -> None:
        assert normalize_year_month(raw) is None

    @pytest.mark.parametrize("raw", ["hier", "2021-13", "1850", "2201-01", "septembre", "xyz 2021"])
    def test_une_forme_inconnue_est_refusee(self, raw: str) -> None:
        with pytest.raises(ValueError, match=r"Date illisible|Mois invalide|Année invraisemblable"):
            normalize_year_month(raw)


class TestNiveaux:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Avancé", SkillLevel.ADVANCED),
            ("avance", SkillLevel.ADVANCED),
            ("EXPERT", SkillLevel.EXPERT),
            ("notions", SkillLevel.BEGINNER),
            ("3", SkillLevel.INTERMEDIATE),
            (4, SkillLevel.ADVANCED),
            (SkillLevel.BEGINNER, SkillLevel.BEGINNER),
        ],
    )
    def test_un_niveau_de_competence_se_ramene_a_lenumeration(
        self, raw: object, expected: SkillLevel
    ) -> None:
        assert coerce_skill_level(raw) is expected

    def test_un_niveau_de_competence_inconnu_est_refuse(self) -> None:
        with pytest.raises(ValueError, match="Niveau de compétence inconnu"):
            coerce_skill_level("cosmique")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Natif", LanguageLevel.NATIVE),
            ("langue maternelle", LanguageLevel.NATIVE),
            ("bilingue", LanguageLevel.NATIVE),
            ("courant", LanguageLevel.C1),
            ("c1", LanguageLevel.C1),
            ("scolaire", LanguageLevel.B1),
            ("notions", LanguageLevel.A2),
            ("A1", LanguageLevel.A1),
            (LanguageLevel.B2, LanguageLevel.B2),
        ],
    )
    def test_un_niveau_de_langue_se_ramene_a_lenumeration(
        self, raw: object, expected: LanguageLevel
    ) -> None:
        assert coerce_language_level(raw) is expected

    def test_un_niveau_de_langue_inconnu_est_refuse(self) -> None:
        with pytest.raises(ValueError, match="Niveau de langue inconnu"):
            coerce_language_level("Z9")


class TestTexte:
    def test_fold_retire_accents_casse_et_espaces(self) -> None:
        assert fold("  Débutant   AVANCÉ ") == "debutant avance"

    def test_les_puces_sont_retirees_et_les_lignes_vides_ignorees(self) -> None:
        assert clean_bullets(["• Conseiller les clients", "- Tenir la caisse ", "", "   "]) == [
            "Conseiller les clients",
            "Tenir la caisse",
        ]
