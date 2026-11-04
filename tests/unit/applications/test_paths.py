"""Les chemins JSON d'une proposition : lire, écrire, refuser ce qui n'existe pas."""

from __future__ import annotations

import pytest

from karria.modules.applications.domain import PathError, is_well_formed, read, write

pytestmark = pytest.mark.unit

CV = {
    "identity": {"first_name": "Awa", "headline": "Vendeuse"},
    "summary": "Bonjour",
    "experiences": [{"position": "Vendeuse", "missions": ["Accueillir", "Encaisser"]}],
    "skills": [{"name": "Caisse"}, {"name": "Conseil"}],
}


class TestForme:
    @pytest.mark.parametrize("path", ["summary", "identity.headline", "experiences.0.missions.1"])
    def test_un_chemin_bien_forme_passe(self, path: str) -> None:
        assert is_well_formed(path)

    @pytest.mark.parametrize("path", ["", "Summary", "a..b", ".a", "a.", "a b", "x" * 81])
    def test_un_chemin_mal_forme_est_refuse(self, path: str) -> None:
        assert not is_well_formed(path)


class TestLecture:
    def test_lit_une_valeur_simple_et_une_valeur_de_liste(self) -> None:
        assert read(CV, "identity.headline") == "Vendeuse"
        assert read(CV, "experiences.0.missions.1") == "Encaisser"
        assert read(CV, "skills") == CV["skills"]

    @pytest.mark.parametrize(
        "path", ["identity.age", "experiences.3.position", "summary.x", "experiences.a", "nope"]
    )
    def test_un_chemin_qui_ne_designe_rien_leve(self, path: str) -> None:
        with pytest.raises(PathError):
            read(CV, path)


class TestEcriture:
    def test_ecrit_sans_toucher_l_original(self) -> None:
        updated = write(CV, "experiences.0.missions.0", "Accueillir et conseiller")

        assert updated["experiences"][0]["missions"][0] == "Accueillir et conseiller"
        assert CV["experiences"][0]["missions"][0] == "Accueillir"

    def test_remplace_une_liste_entiere(self) -> None:
        reordered = [CV["skills"][1], CV["skills"][0]]
        updated = write(CV, "skills", reordered)

        assert updated["skills"] == reordered
        assert updated["skills"] is not reordered

    def test_n_ajoute_jamais_un_element(self) -> None:
        with pytest.raises(PathError):
            write(CV, "experiences.0.missions.2", "Inventé")
        with pytest.raises(PathError):
            write(CV, "identity.age", 30)
