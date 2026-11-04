"""Les mots-clés d'une offre et leur couverture par le CV adapté."""

from __future__ import annotations

import pytest

from doubles import sample_base_cv, sample_offer_card
from karria.modules.applications.domain import Coverage, coverage, cv_text, extract_keywords, fold

pytestmark = pytest.mark.unit


class TestMotsCles:
    def test_tire_sigles_outils_et_groupes_nominaux(self) -> None:
        found = extract_keywords(
            "Technicien de maintenance industrielle",
            "Vous intervenez sur les lignes de production. Habilitation CACES et SAP exigés. "
            "Maintenance préventive, maintenance curative, lecture de plans. Permis B.",
        )

        assert any("caces" in k for k in found)
        assert any("sap" in k for k in found)
        assert any("maintenance" in k for k in found)
        assert "vous" not in found
        assert len(found) <= 15

    def test_le_titre_compte_double(self) -> None:
        found = extract_keywords("Chef de rayon", "Encaissement. Rangement. Inventaire. Livraison.")

        assert found[0] == "chef de rayon"

    def test_une_annonce_vide_ne_rend_rien(self) -> None:
        assert extract_keywords("", "") == []

    def test_pas_plus_que_la_borne(self) -> None:
        text = " ".join(f"outil{i}" for i in range(50))
        assert len(extract_keywords("Poste", text, top=5)) == 5


class TestCouverture:
    def test_dit_ce_que_le_cv_contient_et_ce_qui_manque(self) -> None:
        result = coverage(
            ("relation client", "caisse", "merchandising", "python"), sample_base_cv()
        )

        assert result.present == ("relation client", "caisse", "merchandising")
        assert result.missing == ("python",)
        assert result.ratio == 0.75

    def test_tolere_le_pluriel_la_casse_et_les_accents(self) -> None:
        cv = {"summary": "Gestion des Stocks et encaissements, à l'aise avec Excel"}

        result = coverage(("gestion de stock", "encaissement", "excel", "stock"), cv)

        assert "encaissement" in result.present
        assert "excel" in result.present
        assert "stock" in result.present
        # « gestion des stocks » couvre « gestion de stock » : le pluriel vaut sur chaque mot.
        assert "gestion de stock" in result.present

    def test_un_mot_entier_seulement(self) -> None:
        assert coverage(("vente",), {"summary": "Inventeur"}).missing == ("vente",)
        assert coverage(("c++",), {"summary": "Je code en C++ et en C"}).present == ("c++",)

    def test_sans_mot_cle_la_couverture_est_pleine(self) -> None:
        assert coverage((), {}).ratio == 1.0

    def test_aller_retour_json(self) -> None:
        value = Coverage(present=("a",), missing=("b", "c"))

        assert Coverage.from_json(value.as_json()) == value
        assert Coverage.from_json(None) == Coverage()
        assert value.as_json()["ratio"] == 0.33


class TestTexteDuCv:
    def test_aplatit_tout_sauf_les_identifiants(self) -> None:
        text = cv_text(sample_base_cv())

        assert "Awa" in text
        assert "Tenir la caisse" in text
        assert "11111111-1111" not in text

    def test_fold_retire_accents_et_apostrophes(self) -> None:
        assert fold("Prêt-à-porter d'Été") == "pret a porter d ete"


def test_l_offre_de_test_donne_des_mots_cles_utiles() -> None:
    card = sample_offer_card()
    found = extract_keywords(card.title, card.description)

    assert "conseillere de vente" in found
    assert any("client" in k for k in found)
