"""Point d'entrée ASGI.

Ce fichier ne connaît aucun module métier et n'a aucune raison de changer
quand l'application grandit. C'est l'inverse exact du `main.py` de la V1,
qui portait cinquante lignes d'imports et quarante-et-un `include_router`,
et qu'il fallait donc modifier à chaque ajout de fonctionnalité.
"""

from karria.bootstrap.app import create_app

app = create_app()
