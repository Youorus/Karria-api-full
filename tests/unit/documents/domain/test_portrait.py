"""Le portrait d'exemple d'un modèle : qui, et dans quel accord (`domain/portrait.py`)."""

from __future__ import annotations

from karria.modules.documents.domain.portrait import persona_of, portrait_prompt, seed_of

SPEC = {
    "meta": {"tags": ["sobre", "deux colonnes", "photo"]},
    "page": {"width_mm": 210, "height_mm": 297, "background": "#FFFFFF"},
    "styles": {},
    "header": {"photo": {"sample_file": "sample_photo.png"}, "elements": [], "decorations": []},
    "page_decorations": [
        {"kind": "rect", "x_mm": 0, "y_mm": 0, "w_mm": 70, "h_mm": 297, "fill": "#10375D"}
    ],
    "bands": [],
}
SAMPLE = {
    "identity": {"first_name": "Sacha", "last_name": "Dubois", "headline": "CHARGÉE DE PROJET"}
}


def test_la_personne_de_l_exemple_porte_son_nom_sa_couleur_et_son_ambiance() -> None:
    persona = persona_of(SPEC, SAMPLE, template_name="bleu", version=1)
    assert persona.full_name == "Sacha Dubois"
    assert persona.headline == "CHARGÉE DE PROJET"
    assert persona.primary == "#10375D"
    assert persona.tags == ("sobre", "deux colonnes", "photo")
    assert persona.seed == seed_of("bleu", 1)


def test_la_consigne_est_realiste_accordee_et_sans_texte() -> None:
    prompt = portrait_prompt(persona_of(SPEC, SAMPLE, template_name="bleu", version=1))
    assert "Sacha Dubois" in prompt
    assert "chargée de projet" in prompt
    assert "#10375D" in prompt  # l'accord avec la couleur du modèle
    assert "Aucun texte" in prompt
    assert "3:4" in prompt
    assert "sobre, deux colonnes, photo" in prompt


def test_deux_modeles_donnent_deux_portraits_differents_et_stables() -> None:
    a = portrait_prompt(persona_of(SPEC, SAMPLE, template_name="bleu", version=1))
    b = portrait_prompt(persona_of(SPEC, SAMPLE, template_name="vert", version=1))
    again = portrait_prompt(persona_of(SPEC, SAMPLE, template_name="bleu", version=1))
    assert a != b
    assert a == again


def test_sans_couleur_ni_nom_la_consigne_reste_entiere() -> None:
    spec = {**SPEC, "page_decorations": []}
    prompt = portrait_prompt(persona_of(spec, {}, template_name="nu", version=2))
    assert "gris très clair" in prompt
    assert "la personne de l'exemple" in prompt
