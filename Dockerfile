# =====================================================================
# Image de production. Construction en deux étapes : l'outillage de build
# ne doit jamais se retrouver dans l'image livrée — c'est à la fois du
# poids inutile et de la surface d'attaque.
# =====================================================================

# --- Étape 1 : dépendances -------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Les manifestes sont copiés seuls, avant le code : tant qu'ils ne
# changent pas, Docker réutilise le cache des dépendances. Copier le code
# d'abord invaliderait cette couche à chaque commit.
# `README.md` n'est pas de la documentation ici : `pyproject.toml` le
# déclare comme `readme`, et hatchling refuse de construire le paquet
# sans lui — « Readme file does not exist ». L'oublier faisait échouer
# la construction de l'image à la dernière étape, après plusieurs
# minutes, sur un message qui ne dit pas qu'un fichier manque au COPY.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --extra prod

COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra prod


# --- Étape 2 : exécution ---------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# Utilisateur non privilégié : une exécution en root transforme une
# évasion de conteneur en compromission complète de l'hôte.
RUN groupadd --system --gid 1001 karria \
    && useradd --system --uid 1001 --gid karria --create-home karria

WORKDIR /app

COPY --from=builder --chown=karria:karria /app/.venv /app/.venv
COPY --from=builder --chown=karria:karria /app/src /app/src
COPY --chown=karria:karria migrations/ ./migrations/
COPY --chown=karria:karria pyproject.toml ./

# Les commandes d'exploitation — provisionnement des buckets,
# diagnostic, test SMTP — vivent dans `scripts/`
# (AGENTS.md §6.4). Sans elles dans l'image, la mise en service d'un
# environnement déployé est impossible : l'API refuse de démarrer tant
# que les buckets n'existent pas, et rien à bord ne sait les créer.
COPY --chown=karria:karria scripts/ ./scripts/

USER karria

EXPOSE 8000

# Sonde de vivacité : elle n'interroge aucune dépendance, sinon une panne
# PostgreSQL ferait redémarrer en boucle des conteneurs en parfait état.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live')"

# Gunicorn supervise des workers uvicorn : il gère le redémarrage d'un
# worker mort et l'arrêt en douceur, ce qu'uvicorn seul ne fait pas.
CMD ["gunicorn", "karria.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "60", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-"]
