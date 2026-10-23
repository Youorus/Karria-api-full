"""Quantifie en int8 un modèle ONNX servi par Text Embeddings Inference (TEI).

    docker run --rm -v /opt/karria/tei-cache:/c:ro -v /opt/karria/models:/m \\
      -v "$PWD/scripts":/s python:3.13-slim sh -c \\
      "pip install -q onnxruntime==1.23.2 onnx==1.19.1 && \\
       python /s/quantize_onnx.py \\
         /c/models--intfloat--multilingual-e5-base/snapshots/d128750597153bb5987e10b1c3493a34e5a4502a \\
         /m/e5-base-int8"

Pourquoi ce script existe (ADR-0016) : sur le CPU du serveur (EPYC Milan,
AVX2, sans AVX-512), la quantification dynamique int8 par canal fait passer
`multilingual-e5-base` de 9,7 à 15,3 annonces/s à ~600 caractères, et la
requête de 25 à 9 ms. TEI ne quantifie pas lui-même : il charge
`onnx/model.onnx` tel quel, d'où un dossier de modèle local
(`/opt/karria/models/e5-base-int8`) monté dans le conteneur.

`onnxruntime` n'est **pas** une dépendance du projet : le script tourne
une fois, dans un conteneur jetable, à chaque changement de modèle — et un
changement de modèle impose de toute façon un ré-indexage
(`scripts/offers.py reconcile`), jamais au démarrage.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def main(source: Path, target: Path) -> int:
    """Copie la configuration et le tokenizer, quantifie les poids."""
    from onnxruntime.quantization import QuantType, quantize_dynamic  # noqa: PLC0415

    if target.exists():
        shutil.rmtree(target)
    for path in source.rglob("*"):
        if path.is_file() and "onnx" not in path.parts:
            destination = target / path.relative_to(source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(path.resolve(), destination)
    (target / "onnx").mkdir(parents=True, exist_ok=True)
    work = target / "source.onnx"
    shutil.copy((source / "onnx" / "model.onnx").resolve(), work)
    quantize_dynamic(
        str(work),
        str(target / "onnx" / "model.onnx"),
        weight_type=QuantType.QInt8,
        per_channel=True,
    )
    work.unlink()
    for leftover in target.glob("*inferred*"):
        leftover.unlink()
    print(f"Modèle quantifié : {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
