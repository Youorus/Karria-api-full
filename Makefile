# =====================================================================
# Point d'entrée unique de l'outillage. Un développeur qui rejoint le
# projet n'a besoin de connaître que `make help`. Les commandes exactes
# (uv, ruff, aerich...) sont un détail d'implémentation.
# =====================================================================

.DEFAULT_GOAL := help
.PHONY: help install dev test test-unit test-integration lint format typecheck \
        boundaries check migrate migrate-apply db-up db-down storage-provision \
        doctor up clean

help: ## Affiche cette aide
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- Environnement ---------------------------------------------------

install: ## Installe les dépendances et les hooks pre-commit
	uv sync --all-groups
	uv run pre-commit install

dev: ## Lance l'API en rechargement à chaud
	uv run uvicorn karria.main:app --reload --host 0.0.0.0 --port 8000

# --- Qualité ---------------------------------------------------------

lint: ## Vérifie le style (sans modifier les fichiers)
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts

format: ## Corrige et formate le code
	uv run ruff check --fix src tests scripts
	uv run ruff format src tests scripts

typecheck: ## Vérifie le typage en mode strict
	uv run mypy

boundaries: ## Vérifie les frontières d'architecture
	uv run lint-imports

test: ## Lance toute la suite de tests avec couverture
	uv run pytest --cov --cov-report=term-missing

test-unit: ## Lance uniquement les tests unitaires (rapides)
	uv run pytest tests/unit -m unit

test-integration: ## Lance les tests d'intégration (nécessite `make db-up`)
	uv run pytest tests/integration -m integration

check: lint typecheck boundaries test ## Porte de qualité complète (identique à la CI)

# --- Base de données -------------------------------------------------

db-up: ## Démarre PostgreSQL et MinIO en local
	docker compose up -d postgres minio

db-down: ## Arrête les services locaux
	docker compose down

migrate: ## Génère une migration (usage : make migrate m="ajout table x")
	uv run aerich migrate --name "$(m)"

migrate-apply: ## Applique les migrations en attente
	uv run aerich upgrade

# --- Stockage objet --------------------------------------------------

storage-provision: ## Crée et configure les buckets déclarés au registre
	uv run python scripts/storage_provision.py

# --- Exploitation ----------------------------------------------------

doctor: ## Vérifie que l'environnement est réellement opérationnel
	uv run python -m scripts.doctor

up: ## Monte toute la pile locale, prête à l'emploi, en une commande
	@docker compose up -d postgres minio
	@printf "Attente de PostgreSQL et MinIO"
	@until docker compose exec -T postgres pg_isready -U karria >/dev/null 2>&1; do printf "."; sleep 1; done
	@echo " prêts."
	@uv run aerich upgrade
	@uv run python -m scripts.storage_provision
	@uv run python -m scripts.doctor

# --- Divers ----------------------------------------------------------

clean: ## Supprime les caches et artefacts locaux
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .mypy_cache .ruff_cache .pytest_cache .import_linter_cache htmlcov .coverage coverage.xml
