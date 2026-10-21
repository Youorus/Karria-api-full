"""Les schémas de chapitre : la forme V1, ses invariants, sa normalisation."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from karria.modules.cv.domain import (
    CHAPTER_SCHEMAS,
    Chapter,
    Contact,
    Education,
    Experience,
    Identity,
    Interest,
    Language,
    Link,
    PhotoChapter,
    Project,
    Skill,
    SkillsChapter,
    Summary,
    validate_chapter_payload,
)

pytestmark = pytest.mark.unit


class TestIdentite:
    def test_les_blancs_sont_reduits_et_le_titre_vide_vaut_none(self) -> None:
        identity = Identity(first_name="  Awa ", last_name="Diallo", headline="   ")
        assert identity.first_name == "Awa"
        assert identity.headline is None

    def test_un_prenom_vide_est_refuse(self) -> None:
        with pytest.raises(ValidationError):
            Identity(first_name=" ", last_name="Diallo")


class TestContact:
    def test_lemail_est_normalise_et_les_liens_completes(self) -> None:
        contact = Contact(
            email=" Awa.Diallo@Gmail.com ",
            links=[Link(label="LinkedIn", url="linkedin.com/in/awa")],
        )
        assert contact.email == "awa.diallo@gmail.com"
        assert contact.links[0].url == "https://linkedin.com/in/awa"

    def test_un_email_mal_forme_est_refuse(self) -> None:
        with pytest.raises(ValidationError, match="Adresse e-mail invalide"):
            Contact(email="pas-un-email", phone="06")

    def test_un_lien_hors_http_est_refuse(self) -> None:
        with pytest.raises(ValidationError, match="http"):
            Link(label="x", url="javascript:alert(1)")

    def test_il_faut_un_email_ou_un_telephone(self) -> None:
        with pytest.raises(ValidationError, match="e-mail ou un téléphone"):
            Contact(city="Montreuil")

    def test_un_telephone_seul_suffit(self) -> None:
        assert Contact(phone="06 12 34 56 78", email="").email is None


class TestExperience:
    def test_les_missions_en_texte_deviennent_des_puces_et_lid_est_attribue(self) -> None:
        experience = Experience(
            position="Vendeuse",
            company="Zara",
            missions="• Conseiller les clients\n- Tenir la caisse\n",  # type: ignore[arg-type]
            start_date="septembre 2022",
            end_date="2023-06-30",
        )
        assert experience.missions == ["Conseiller les clients", "Tenir la caisse"]
        assert experience.start_date == "2022-09"
        assert experience.end_date == "2023-06"
        assert isinstance(experience.id, UUID)

    def test_une_experience_en_cours_na_pas_de_date_de_fin(self) -> None:
        experience = Experience(position="x", company="y", is_current=True, end_date="2023-01")
        assert experience.end_date is None

    def test_une_fin_avant_le_debut_est_refusee(self) -> None:
        with pytest.raises(ValidationError, match="précède"):
            Experience(position="x", company="y", start_date="2023-01", end_date="2022-01")

    def test_une_date_illisible_est_refusee(self) -> None:
        with pytest.raises(ValidationError, match="Date illisible"):
            Experience(position="x", company="y", start_date="l'an dernier")

    def test_un_id_fourni_est_conserve(self) -> None:
        given = UUID("12345678-1234-5678-1234-567812345678")
        assert Experience(id=given, position="x", company="y").id == given


class TestFormation:
    def test_une_formation_en_cours_na_pas_de_date_de_fin(self) -> None:
        education = Education(degree="BTS", school="Lycée", is_in_progress=True, end_date="2025")
        assert education.end_date is None

    def test_une_fin_avant_le_debut_est_refusee(self) -> None:
        with pytest.raises(ValidationError, match="précède"):
            Education(degree="BTS", school="Lycée", start_date="2023", end_date="2021")


class TestCompetences:
    def test_les_niveaux_libres_sont_ramenes_aux_enumerations(self) -> None:
        chapter = SkillsChapter(
            skills=[Skill(name="Vente", level="maîtrise")],  # type: ignore[arg-type]
            languages=[Language(language="Anglais", level="courant")],  # type: ignore[arg-type]
        )
        assert chapter.skills[0].level.value == "Avancé"
        assert chapter.languages[0].level.value == "C1"

    def test_un_niveau_inconnu_est_refuse(self) -> None:
        with pytest.raises(ValidationError, match="Niveau de compétence inconnu"):
            Skill(name="Vente", level="cosmique")  # type: ignore[arg-type]

    def test_la_categorie_vide_vaut_none(self) -> None:
        assert Skill(name="Vente", level="Expert", category="").category is None  # type: ignore[arg-type]


class TestAutresChapitres:
    def test_le_resume_vide_est_refuse(self) -> None:
        with pytest.raises(ValidationError):
            Summary(summary="  ")

    def test_un_interet_garde_sa_categorie(self) -> None:
        interest = Interest(name="Danse", category="Sport", description=" ")
        assert interest.category == "Sport"
        assert interest.description is None

    def test_la_photo_accepte_aucune_image(self) -> None:
        assert PhotoChapter().image_id is None

    def test_un_projet_complete_ses_liens(self) -> None:
        project = Project(title="Site", link="monsite.fr", github="", technologies=["React"])
        assert project.link == "https://monsite.fr"
        assert project.github is None


class TestValidationParChapitre:
    def test_chaque_chapitre_a_un_schema(self) -> None:
        assert set(CHAPTER_SCHEMAS) == set(Chapter)

    def test_la_validation_rend_un_dictionnaire_json_normalise(self) -> None:
        data = validate_chapter_payload(
            Chapter.IDENTITY,
            {"first_name": " Awa", "last_name": "Diallo", "headline": None, "x": 1},
        )
        assert data == {"first_name": "Awa", "last_name": "Diallo", "headline": None}

    def test_une_valeur_invalide_leve_une_erreur_pydantic(self) -> None:
        with pytest.raises(ValidationError):
            validate_chapter_payload(Chapter.SUMMARY, {"summary": ""})


def test_des_missions_qui_ne_sont_ni_texte_ni_liste_sont_refusees() -> None:
    with pytest.raises(ValidationError):
        Experience(position="x", company="y", missions=42)  # type: ignore[arg-type]
