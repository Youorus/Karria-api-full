"""Le schéma JSON imposé à un modèle, dérivé d'un modèle Pydantic — sobre et autonome.

Pydantic produit des `$ref` vers `$defs`, des `title`, des `default` et
des `format` ; les fournisseurs veulent un schéma autonome et sobre. On
déréférence, on retire le bruit, et on retire les champs `id` : ils sont
attribués par nous à la validation, le modèle n'a pas à les inventer.

Les bornes de longueur (`minLength`, `maxLength`, `minItems`, `maxItems`)
sont retirées aussi : constaté le 2026-10-21, Gemini refuse le schéma
complet d'extraction de CV tant qu'elles y figurent — et Pydantic les
applique de toute façon à la revalidation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, cast

from pydantic import BaseModel

#: Clés de schéma que les fournisseurs ne connaissent pas ou interprètent mal.
SCHEMA_NOISE: Final[frozenset[str]] = frozenset(
    {"title", "default", "format", "minLength", "maxLength", "minItems", "maxItems"}
)


def response_schema_for(
    model: type[BaseModel], *, drop_fields: frozenset[str] = frozenset({"id"})
) -> dict[str, Any]:
    """Le schéma JSON à imposer, dérivé d'un modèle Pydantic."""
    schema = model.model_json_schema()
    definitions = schema.pop("$defs", {})
    return cast(dict[str, Any], clean_schema(schema, definitions, drop_fields))


def clean_schema(
    node: Any, definitions: Mapping[str, Any], drop_fields: frozenset[str] = frozenset()
) -> Any:
    """Déréférence, retire le bruit et les champs indésirables, récursivement."""
    if isinstance(node, dict):
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            return clean_schema(definitions[name], definitions, drop_fields)
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key in SCHEMA_NOISE:
                continue
            if key == "properties" and isinstance(value, dict):
                cleaned[key] = {
                    name: clean_schema(prop, definitions, drop_fields)
                    for name, prop in value.items()
                    if name not in drop_fields
                }
            elif key == "required" and isinstance(value, list):
                cleaned[key] = [name for name in value if name not in drop_fields]
            else:
                cleaned[key] = clean_schema(value, definitions, drop_fields)
        return cleaned
    if isinstance(node, list):
        return [clean_schema(item, definitions, drop_fields) for item in node]
    return node


def strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """La variante « stricte » qu'OpenAI et Anthropic attendent : `additionalProperties: false` partout.

    Les champs facultatifs y deviennent obligatoires mais nullables, parce que
    le mode strict exige que `required` liste toutes les propriétés.
    """

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items()}
            if out.get("type") == "object" and isinstance(out.get("properties"), dict):
                out["additionalProperties"] = False
                out["required"] = list(out["properties"])
            return out
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return cast(dict[str, Any], walk(dict(schema)))
