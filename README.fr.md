# IterWave — Espace de gestion de contenu visuel assisté par IA

[简体中文](README.md) · [English](README.en.md) · [Русский](README.ru.md) · [日本語](README.ja.md) · [Français](README.fr.md) · [Deutsch](README.de.md)

> **Early Preview** — Projet open source en développement actif. L’état réel des fonctionnalités doit être évalué à partir du code, des tests et des validations dans l’environnement cible.

IterWave est un espace de travail destiné aux créateurs qui publient du contenu mêlant texte et images sur plusieurs plateformes. Il relie la découverte de sujets, la planification, la production adaptée à chaque plateforme, le contrôle qualité, la validation humaine avant publication et l’analyse des résultats dans un flux traçable.

## Principes fondamentaux

- **Priorité au contenu visuel :** sujets, textes, cartes illustrées, contrôle qualité et analyse opérationnelle.
- **Production pilotée par les plans :** le plan principal, les sous-plans par plateforme, les dépendances entre étapes et les états d’exécution pilotent la production.
- **Publication contrôlée par un humain :** l’automatisation prépare et enregistre les brouillons, mais ne clique jamais sur le bouton de publication.
- **Traçabilité :** versions des plans et contenus, états des tâches, résultats d’exécution et éléments justificatifs sont conservés.
- **Contrôle local :** l’exploitant maîtrise les identifiants, la configuration, la base de données et les ressources de contenu.

## Flux de travail

```text
Bibliothèque de sujets / Positionnement du compte / Tendances
                            ↓
                      Plan principal et sous-plans
                            ↓
                    Orchestration et dépendances des étapes
                            ↓
                  Textes et cartes adaptés aux plateformes
                            ↓
                        Contrôle qualité et brouillon
                            ↓
                       Publication validée par l’utilisateur
                            ↓
                 Collecte des données → Analyse → Itération
```

## Plateformes prises en charge

Xiaohongshu, Douyin, Kuaishou et WeChat Channels (contenu texte-image). Les capacités réelles dépendent des interfaces, des autorisations des comptes et des changements d’interface ; les parcours avec de vrais comptes doivent être validés dans l’environnement cible.

## Technologies

| Couche | Technologies |
|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic v2 |
| Stockage | SQLite, migrations de schéma versionnées |
| Planification et exécution | APScheduler, file globale de tâches, Workers isolés |
| Automatisation du navigateur | Playwright (enregistrement de brouillons et collecte en lecture seule) |
| Frontend | React 18, TypeScript, Vite |
| Traitement d’images | Pillow, OpenCV, NumPy |

## Démarrage rapide

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
uvicorn src.api.main:app --reload
```

Préparez la configuration à partir de `config/config.yaml`. Conservez les clés API, cookies et jetons dans la configuration locale de l’environnement ; ne les ajoutez jamais au dépôt.

### Frontend

```bash
cd web
npm install
npm run dev
npm test
npm run build
```

## Déploiement et sécurité

- Avant le premier déploiement, définissez un `ITERWAVE_BOOTSTRAP_TOKEN` aléatoire et robuste, puis configurez le mot de passe d’accès définitif via le processus d’initialisation. Supprimez le jeton d’amorçage après l’initialisation.
- Utilisez HTTPS pour tout déploiement public. N’activez pas `ITERWAVE_ALLOW_LOCAL_BOOTSTRAP=1` en environnement public.
- Limitez l’accès aux répertoires de configuration, de base de données et de sessions à l’utilisateur système du service.
- APScheduler ne gère pas encore l’élection d’un leader entre processus. Utilisez un seul worker Uvicorn ; ne configurez pas plusieurs `--workers`.
- Par défaut, la file globale utilise un Worker et une exécution séquentielle FIFO. N’activez le parallélisme entre groupes de comptes qu’après avoir évalué l’impact du délai maximal au niveau du processus.
- Les tâches interrompues par un délai maximal ou l’arrêt d’un Worker ne sont pas relancées automatiquement. Vérifiez d’abord le résultat sur la plateforme.
- Les commentaires, réponses et abonnements automatisés sont désactivés par défaut. Vérifiez les règles de la plateforme et les autorisations du compte avant activation.
- L’automatisation Playwright s’arrête à l’enregistrement du brouillon. La publication effective doit être confirmée par l’utilisateur.

## Structure du dépôt

```text
src/
  core/       Machines à états, versions des plans, file de tâches, planification, reprise, analyses
  adapters/   Adaptateurs de plateformes
  api/        Routes FastAPI et cycle de vie de l’application
  vendor/     Code tiers conservé sous sa licence d’origine
web/          Frontend React
migrations/   Migrations du schéma de base de données
tests/        Tests backend
config/       Modèles de configuration sans secrets réels
```

## État du développement et validations

Le projet est toujours en développement. La réussite des tests du dépôt ne garantit pas une validation de bout en bout avec les plateformes réelles. Les éléments suivants doivent être vérifiés dans un environnement disposant des comptes, du réseau et des identifiants de modèles nécessaires :

- Connexion réelle, enregistrement des brouillons et lecture des données sur les quatre plateformes ;
- Chaîne complète des modèles : génération de textes, création de cartes illustrées et contrôle qualité ;
- Boucle complète allant de la collecte des données après publication à l’analyse et à l’exécution de la stratégie suivante.

Toute contribution doit inclure les tests pertinents. Ne stockez pas d’identifiants, de données utilisateur ou d’état de comptes réels dans les journaux ou le dépôt.

## Code tiers et licence

Certains adaptateurs de plateformes sont copiés et modifiés à partir de [Easel](https://github.com/kaichen/Easel) (Apache License 2.0). Consultez `src/vendor/easel/LICENSE` et `src/vendor/easel/NOTICE` pour les mentions et les conditions de licence.

Ce projet est distribué sous licence Apache License 2.0. Voir [LICENSE](LICENSE).
