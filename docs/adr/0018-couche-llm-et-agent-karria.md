# ADR-0018 — Une couche LLM indépendante du fournisseur, et Karria : constitution, mémoire, garde-fous

**Statut :** Proposée — 2026-10-23 (à valider par Marc)
**Complète :** ADR-0013 (Gemini par port, anti-injection), ADR-0016 et 0017 (moteur d'offres, planificateur)
**Contexte :** `shared/llm`, modules `cv` et `offers`

## Le problème

Trois besoins arrivent ensemble :

1. **Changer de modèle ou de fournisseur sans livraison.** L'ADR-0013 avait
   un client Gemini par module (`cv/infrastructure/gemini.py`, un second
   dans `offers`) ; un autre fournisseur aurait voulu un troisième client,
   et le choix du modèle se faisait dans le code de chaque adaptateur.
2. **Un agent ouvert mais tenu.** Karria doit parler naturellement (marché,
   métiers, CV, entretien, moral, l'application) et pas seulement
   transcrire des critères — sans jamais cesser d'être Karria : ni nommer
   son modèle, ni révéler sa consigne, ni suivre une instruction glissée
   dans un message, ni inventer une offre. Et cette tenue ne doit **pas
   dépendre de la conversation** : au cinquantième tour comme au premier.
3. **Mettre du sens là où les règles n'en portent pas**, à coût et latence
   contrôlés : la porte d'intitulé refusait un « commis de cuisine » pour
   « cuisinier » seulement si un mot de niveau le trahissait ; les champs
   manquants des annonces (temps, niveau, contrat) se devinaient par regex.

## Ce qu'on décide

### 1. Un port, quatre adaptateurs, un routeur par tâche (`shared/llm`)

- **Le port** `LanguageModel.generate(LlmRequest) -> LlmResult` : la tâche,
  la consigne de système, des parties texte ou binaires (`Blob` : audio,
  image, PDF), un schéma JSON imposé, la température, un plafond de sortie,
  la réflexion (coupée par défaut), l'image en sortie. Aucun fournisseur
  n'y apparaît. `structured()` revalide la sortie avec Pydantic : la sortie
  reste une **donnée** (ADR-0013).
- **Quatre adaptateurs**, par l'API REST et le client sortant du socle, sans
  SDK (la raison de l'ADR-0013 tient : une douzaine de paquets par
  fournisseur et une clé lue à notre insu) :
  - `GeminiAdapter` — `generateContent`, inline_data, image en sortie ; le
    seul à porter toutes les capacités ;
  - `AnthropicAdapter` — `/v1/messages`, `output_config.format` à schéma
    strict, blocs `image` et `document` ; **pas d'audio ni d'image en
    sortie** (`LlmCapabilityError`) ; réflexion et température non
    envoyées (les modèles 5.x les refusent), `effort: low` pour le coût ;
  - `OpenAIAdapter` — `/chat/completions`, `response_format` à schéma
    strict, `image_url`, `input_audio` (wav, mp3), `file` pour un PDF ;
  - le même adaptateur en mode **`compatible`** (vLLM, Mistral, un modèle
    local) : une autre `base_url`, une clé facultative, et un repli
    `json_object` + schéma rappelé dans la consigne quand le serveur ne
    sait pas imposer un schéma.
- **Le routeur** : `LLM__TASKS__<TÂCHE>=fournisseur:modèle` pour
  `search_dialogue`, `cv_interpret`, `cv_extract`, `transcribe`, `portrait`,
  `enrich`, `memory` ; `LLM__FALLBACKS__<TÂCHE>` pour un repli sur panne
  ou capacité absente (jamais sur un refus de contenu ni une sortie hors
  forme : ce n'est pas une panne). Les clés : `LLM__PROVIDERS__<F>__API_KEY`.
- **Validé au démarrage.** En environnement déployé, une route explicite
  mal formée ou vers un fournisseur sans clé **empêche le démarrage** avec
  le nom de la variable fautive. Une route laissée par défaut (Gemini) sans
  clé n'est qu'une IA absente, qui avertit sans bloquer — le comportement
  d'avant. Rétrocompatibilité : `GEMINI__API_KEY` et `GEMINI__TEXT_MODEL`
  restent la source des routes par défaut.
- Les modules ne voient que le port, injecté (`LanguageModelDep`) ; leurs
  adaptateurs (`LlmChapterInterpreter`, `LlmTranscriber`, `LlmCvExtractor`,
  `LlmPortraitEnhancer`, `LlmDialogue`, `LlmMemoryUpdater`) ne nomment que
  leur **tâche** et traduisent les erreurs du port dans les leurs.

### 2. Le banc d'essai des modèles (`scripts/llm_bench.py`)

Trois tâches réelles, les prompts et schémas de la production : les 36
demandes du dialogue (qualité : cherchable au premier tour et le métier
attendu dans un intitulé), six réponses de CV dont deux injections
(qualité : proposition conforme au schéma du chapitre, injection reconnue),
un CV en texte (identité, deux expériences, une formation). Latence de bout
en bout et jetons du fournisseur ; le coût se déduit des jetons avec la
grille du jour, qui n'est pas dans le code.

Résultats du 2026-10-22, modèles disponibles sur la clé :

| modèle | dialogue : réussite / p50 / p95 | interprétation CV : réussite / p50 | extraction CV : réussite / p50 | jetons en entrée (dialogue) |
|---|---|---|---|---|
| **gemini-3.8-flash** (défaut) | **35/36** / 4,4 s / 9,2 s | 6/6 / 2,2 s | 1/1 / 3,8 s | 1 569 |
| gemini-3.7-flash | 35/36 / 4,6 s / 5,6 s | 6/6 / 2,0 s | 1/1 / 4,0 s | 1 569 |
| gemini-3.6-flash | 15/36 / 2,1 s / 3,4 s | 6/6 / 1,5 s | 1/1 / 2,7 s | 1 569 |
| gemini-3.5-flash | 21/36 / 2,3 s / 3,1 s | **6/6 / 1,3 s** | 1/1 / 2,2 s | 1 569 |
| gemini-3.1-flash-lite | 3/36 / 1,4 s | 6/6 / 1,1 s (p95 8,5 s) | 1/1 / 2,1 s | 1 569 |
| gemini-4-argon | 35/36 / 8,4 s / 14,2 s | 6/6 / 4,1 s (p95 22,8 s) | 1/1 / 7,6 s | 3 427 |
| gemini-3.5-flash-lite, gemini-3.1-pro-preview | 0/36 : erreur du fournisseur à chaque appel (schéma JSON ou modèle non servi sur cette clé) | — | — | — |

Le cas « manqué » par les trois meilleurs (« un job étudiant le week-end à
Paris ») n'est pas une erreur : le modèle pose la question du métier, ce
que la constitution lui demande. Les modèles 3.5 et 3.6 perdent le **lieu**
une fois sur deux sur le dialogue (critères sans `location`), ce qui les
disqualifie pour cette tâche malgré leur vitesse ; sur l'interprétation
d'un chapitre de CV, courte et cadrée, ils sont parfaits et deux fois plus
rapides. Les jetons d'entrée du dialogue sont passés à ~2 440 avec la
constitution (§ 3) : la latence mesurée en suite d'attaques reste de 2 à 7 s.

**Décision de routage** : `search_dialogue`, `cv_extract`, `transcribe`, `memory`, `enrich` →
`gemini:gemini-3.8-flash` (la qualité d'abord ; 3.7-flash est l'équivalent
de secours, `LLM__FALLBACKS__SEARCH_DIALOGUE=gemini:gemini-3.7-flash`) ;
`cv_interpret` → **recommandé** `gemini:gemini-3.5-flash` (6/6 à 1,3 s au
lieu de 2,2 s ; six cas seulement : à confirmer sur une semaine de
journaux avant de changer la valeur par défaut) ; `portrait` →
`gemini:gemini-3.1-flash-image`. Aucun fournisseur autre que Gemini n'a de
clé aujourd'hui : les adaptateurs Anthropic et OpenAI sont testés sans
réseau et attendent une clé pour un banc d'essai comparatif.

### 3. Karria : la constitution, épinglée à chaque appel

`offers/infrastructure/constitution.py` — un texte **versionné**
(`CONSTITUTION_VERSION = "1.0"`), relu, qui dit : qui est Karria
(l'assistante de l'application Karria, créée par l'équipe Karria — jamais
un autre rôle, jamais le nom d'une technologie ni d'un fournisseur, jamais
sa consigne), sa mission (la recherche d'offres), ce dont elle parle
volontiers en ramenant vers la recherche (marché, métiers, CV, entretien,
moral, l'application), ce qu'elle décline brièvement et sans leçon
(devoirs, code, recettes, blagues, célébrités, politique, médical,
juridique, tout contenu dangereux), la règle des offres (jamais inventées,
jamais un lien ni un numéro : elles sont dans le fil), son ton (tutoiement,
deux ou trois phrases).

Le serveur l'envoie en consigne de système à **chaque** appel du dialogue,
devant les règles de la tâche ; le texte de la personne voyage entre
balises `<message>`, sa mémoire entre `<memoire>`, comme des données. La
version est journalisée à chaque tour (`offers.conversation_turn`).

### 4. La mémoire de Karria sur la personne, séparée de la politique

`offers_karria_memory` : des **faits** bornés (`MemoryFacts` : métiers visés,
lieux et mobilité, contraintes, préférences, situation, résumé ; 8
entrées de 120 caractères par liste, 600 pour le résumé). Après chaque tour
utile, un travail de file (`offers.update_memory`, hors du chemin de la
réponse) demande au modèle (tâche `memory`, schéma imposé) ce que ce tour
apporte de stable ; la **règle** fusionne et borne (`merge_memory`). Le
contexte d'un tour = constitution + mémoire rendue + **8 messages
récents** (`RECENT_WINDOW`), jamais la conversation entière : Karria se
souvient de la personne sans que son identité dérive avec la longueur de
l'échange. La personne lit sa mémoire (`GET /assistant/memory`) et l'efface
(`DELETE /assistant/memory`) ; la suppression de compte l'efface aussi.

### 5. Les couches déterministes autour du modèle (`domain/guardrails.py`)

- **Avant l'appel**, `pre_classify` : une **question d'identité** reçoit la
  réponse de la constitution **sans appel au modèle** (la plus robuste des
  réponses) ; une **injection** et un **hors-sujet évident** sont signalés
  (journal, `guarded`) et le modèle répond sous la constitution.
- **Le schéma** : la sortie du dialogue porte une **intention** parmi
  `search`, `refine`, `career_talk`, `app_help`, `small_talk`, `off_topic` ;
  le domaine (`decide`) cherche pour les deux premières, répond sans
  toucher aux critères pour les trois suivantes, décline avec un gabarit
  pour la dernière.
- **Après l'appel**, `guard_reply` : une réponse qui nomme un fournisseur ou
  un modèle (Gemini, Google, Claude, Anthropic, OpenAI, GPT, Mistral…), qui
  laisse fuir la consigne (ses marqueurs), ou qui cite un lien ou un numéro
  d'offre est **remplacée** par un gabarit sûr et journalisée
  (`offers.guard_replaced`). Les offres ne viennent que du fil.
- **La porte de non-régression** : `scripts/assistant_redteam.py`, 36 cas
  réels (identité, injections, hors-sujet, bavardage, carrière, recherche)
  dont deux attaques en 5 et 6 tours où l'on tente de faire oublier à
  Karria qui elle est ; critères déterministes ; rapport versionné dans
  `docs/evals/assistant_redteam.json` ; aussi un test d'intégration à la
  demande (`ASSISTANT_REDTEAM_LIVE=1`).

Résultat du 2026-10-23 : **36/36 cas réussis (100 %)** sous `gemini:gemini-3.8-flash`, constitution
1.0 : identité 6/6 (dont 5 répondues sans appel au modèle), injections
7/7, hors-sujet 9/9, bavardage 3/3, carrière 5/5, aide à l'app 2/2,
recherche 2/2, attaques en plusieurs tours 2/2 (6 tours de jeu de rôle
« Max », 5 tours de « nouvelle règle vive OpenAI »). Aucune réponse n'a
nommé un fournisseur ; les garde-fous de sortie n'ont eu rien à remplacer
sur cette passe — ils sont la ceinture, la constitution les bretelles.
Rapport : `docs/evals/assistant_redteam.json`.

### 6. Le sens là où les règles n'en portent pas

- **La porte d'intitulé** reçoit du modèle, dans le même appel et sans
  coût, les **intitulés voisins à exclure** (`excluded_titles` : autres
  niveaux et métiers proches qui partagent des mots) ; le repli
  déterministe reste une liste de **mots de niveau** (chef, second,
  adjoint, aide, assistant, responsable, directeur, stagiaire…) absents de
  la demande. Mesuré (ADR-0017 § Mesures, juge informé) : précision@10
  **82,2 % → 91,4 %** (France Travail 83 → 93 %, HelloWork 85 → 90 %,
  Indeed 78 → 93 %) ; « cuisinier en temps plein dans toute la France »
  7/10 → **10/10**.
- **L'enrichissement des annonces**, conçu *gratuit d'abord* :
  1. les champs structurés de la source quand elle les donne (France
     Travail : contrat, temps, expérience, coordonnées — aucun appel) ;
  2. les règles de `normalization.py` pour ce que le texte dit clairement ;
  3. des **classifieurs légers sur les plongements déjà calculés** (coût
     marginal nul), entraînés sur les annonces France Travail étiquetées.
     Mesuré (`docs/evals/classifiers_e5.md`, 20 000 offres, régression
     logistique sur e5 + n-grammes de titre, 80/20 stratifié) — avec une
     **fuite découverte en passant** : `embedding_text()` écrit la ligne des
     traits (contrat, temps, expérience) dans le texte plongé, donc un
     classifieur sur le vecteur *indexé* relit l'étiquette (92 à 95 %,
     chiffres trompeurs). Les seuls chiffres valables pour une offre au
     champ manquant sont ceux d'un plongement **sans** traits :

     | champ | exactitude (majoritaire) | couverture à ≥ 0,8 / exactitude | verdict |
     |---|---|---|---|
     | temps de travail | 88,1 % (81,8 %) | 80 % / 92,7 % | utile |
     | télétravail (mention) | 95,0 % (94,4 %) | 93 % / 97,1 % | utile, classe rare mal rappelée (25 %) |
     | type de contrat | 76,2 % (52,4 %) | 51 % / 89,8 % | moitié des offres seulement ; `stage` impossible (absent de FT) |
     | expérience | 62,7 % (51,7 %) | 11 % / 90,9 % | inutile : résidu au modèle |

     Sur Indeed/HelloWork (étiquettes regex, proxy bruité) : accord 91 %
     (temps) et 70 % (contrat) au-dessus de 0,8. Coût : entraînement < 25 s,
     inférence 0,03 s pour 1 000 offres, modèles < 200 Ko.
  4. le modèle **pour le résidu seulement** (prédictions sous le seuil de
     confiance, titres marketing), par lots, en cache (condensat de contenu
     + version du prompt), asynchrone, jamais sur le chemin de l'ingestion.

  Ce qui est livré dans cette ADR : les mesures des classifieurs et le
  routage (`LLM__TASKS__ENRICH`). Ce qui attend la décision de Marc :
  la dépendance `scikit-learn` dans l'image (AGENTS.md § 6.11) ; retirer
  la ligne des traits de `embedding_text()` (un ré-indexage complet,
  ~50 min sur le serveur d'inférence, et la recherche perd un signal
  explicite de contrat/temps dans le vecteur dense — à mesurer avant) ou
  calculer un second plongement « propre » par offre (double le coût
  d'inférence) ; puis l'écriture du travail d'enrichissement du résidu —
  `PLUS-TARD.md`.

### 7. Ce que ça coûte

Mesuré sur le banc d'essai et la suite d'attaques (jetons du fournisseur) :

| appel | entrée | sortie | latence p50 |
|---|---|---|---|
| tour de dialogue (constitution + mémoire + fenêtre) | ~2 450 jetons | ~100 jetons | 2 à 7 s (3.8-flash) |
| mise à jour de la mémoire (hors chemin de la réponse) | ~700 jetons | ~80 jetons | ~1,5 s, asynchrone |
| interprétation d'un chapitre de CV | ~630 | ~90 | 2,2 s (1,3 s en 3.5-flash) |
| extraction d'un CV en texte | ~640 | ~360 | 3,8 s |

Le dialogue coûte donc **deux appels par tour utile** (le second en file,
invisible pour la personne) ; une question d'identité ou un hors-sujet
évident n'en coûte qu'un, ou zéro. À la grille Gemini Flash du jour (de
l'ordre de 0,1 à 0,5 $ par million de jetons d'entrée selon le palier,
non versionnée ici), un tour revient à quelques dixièmes de centime ;
1 000 tours par jour font moins d'un euro.

**Enrichissement à 60 000 annonces par jour** (projection, § 6) :
(a) *gratuit d'abord* — France Travail (~40 %, 24 000 annonces) sans
appel ; règles ; les classifieurs couvrent, parmi les 36 000 annonces
JobSpy, ~80 % du temps de travail et ~50 % du contrat au-dessus du seuil
0,8 (chiffres « propres » ci-dessus) ; une annonce part au modèle dès qu'un
champ demandé reste sous le seuil, donc le résidu est dicté par le champ le
plus dur : **~50 % de JobSpy = ~18 000 annonces** (contrat) ou **~90 %** si
l'on veut aussi l'expérience. Par lots de 10 annonces (~2 500 jetons par
appel) : 1 800 à 3 200 appels → 4,5 à 8 M jetons par jour, soit de l'ordre
de **0,5 à 4 € par jour** selon le palier ; (b) *le modèle sur tout* :
60 000 annonces → 6 000 appels → ~15 M jetons par jour, 2 à 8 € par jour,
et deux à trois fois plus de latence de file. Si l'expérience n'est pas
exigée pour JobSpy (elle est déjà structurée chez France Travail), le
gratuit-d'abord divise la facture par trois. L'ingestion elle-même n'attend jamais
l'enrichissement. Le palier gratuit de Google pour un modèle Flash-Lite
n'a pas été vérifié ici (limites de débit et conditions d'usage des
données publiques) : à vérifier avant de s'y appuyer, et jamais pour les
conversations des personnes.

## Conséquences

- Variables : `LLM__TASKS__*`, `LLM__FALLBACKS__*`, `LLM__PROVIDERS__*`
  (`.env.example`). Rien à changer pour une configuration d'avant l'ADR.
- Migration `5_…_memoire_de_karria` ; routes `GET/DELETE /assistant/memory` ;
  `intent` dans la réponse de l'assistant. Le tutoiement remplace le
  vouvoiement dans toutes les réponses de Karria.
- Toute modification de la constitution, du prompt de tâche ou des
  garde-fous se rejoue contre la suite d'attaques avant d'être livrée.
- Ce qui reste ouvert (`PLUS-TARD.md`) : l'enrichissement du résidu, la
  réécriture des cibles rétrogradées et la boucle des synonymes par le
  modèle, un quota et un suivi de coût par personne.

## Alternatives écartées

- **Les SDK des fournisseurs** — même raison que l'ADR-0013, multipliée par
  quatre.
- **Laisser le modèle décider seul de son identité** (consigne seulement)
  — c'est ce que la suite d'attaques met en défaut : une couche
  déterministe après la réponse coûte zéro appel et ne se fatigue pas.
- **Garder toute la conversation en contexte** — c'est ainsi qu'une
  identité dérive ; la mémoire bornée et la fenêtre courte font le même
  service pour la personne, sans ce risque, et pour moins de jetons.
- **Un modèle auto-hébergé sur les 8 vCPU partagés** pour l'enrichissement
  — écarté sans mesure : le serveur d'inférence de plongements y tient
  déjà 20 offres/s ; un modèle de langage de 7 milliards de paramètres sur
  CPU ne rendrait que quelques jetons par seconde, loin de 60 000 annonces
  par jour. À reconsidérer si une mesure disait autrement.
