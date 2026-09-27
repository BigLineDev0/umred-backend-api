# UMRED — Backend

API REST du projet UMRED, plateforme de gestion des laboratoires de recherche de l'UMRED (Unité Mixte de Recherche d'Exploration et de Diagnostic), rattachée à l'UFR Santé de l'Université Iba Der Thiam de Thiès.

Ce dépôt contient le service backend principal, construit avec Django REST Framework. Il gère l'authentification, les réservations, les équipements, la maintenance, les consommables, les notifications et le journal d'activité. Il est consommé par deux clients : l'application Angular (interface utilisateur) et le service FastAPI dédié à l'assistant conversationnel, qui interroge cette API pour lire et écrire les données réelles du laboratoire.

## Sommaire

- [Architecture](#architecture)
- [Prérequis](#prérequis)
- [Installation](#installation)
- [Variables d'environnement](#variables-denvironnement)
- [Lancement](#lancement)
- [Structure du projet](#structure-du-projet)
- [Modèle de rôles et règles métier](#modèle-de-rôles-et-règles-métier)
- [Authentification](#authentification)
- [Documentation de l'API](#documentation-de-lapi)
- [Automatisations n8n](#automatisations-n8n)
- [Tests](#tests)
- [Déploiement](#déploiement)

## Architecture

Le projet UMRED repose sur trois services indépendants :

| Service | Rôle | Technologie |
|---|---|---|
| `backend` (ce dépôt) | Logique métier, persistance des données, API REST | Django REST Framework, PostgreSQL |
| `frontend` | Interface utilisateur | Angular |
| `umred-labo-ia` | Assistant conversationnel | FastAPI, modèle Hugging Face |

Le service IA ne possède pas sa propre base de données : il s'authentifie auprès de ce backend avec un jeton JWT et appelle les mêmes endpoints que le frontend Angular (réservations, équipements, disponibilités). Toute logique métier ajoutée ici (règles de validation, calculs de conflit, alertes) est donc automatiquement disponible pour les deux clients.

## Prérequis

- Python 3.12 ou supérieur
- PostgreSQL 14 ou supérieur
- Un compte Gmail (ou tout autre fournisseur SMTP) pour l'envoi d'e-mails transactionnels
- Un compte n8n, local ou hébergé, pour les workflows d'automatisation (rappels, notifications)

## Installation

```bash
git clone <url-du-depot>
cd backend

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

Copiez le fichier d'exemple et renseignez vos propres valeurs :

```bash
cp .env.example .env
```

Créez la base de données PostgreSQL, puis appliquez les migrations :

```bash
python manage.py migrate
python manage.py createsuperuser
```

## Variables d'environnement

| Variable | Description |
|---|---|
| `SECRET_KEY` | Clé secrète Django, utilisée aussi pour signer les jetons JWT. Doit être strictement identique à `JWT_SECRET_KEY` dans le service `umred-labo-ia`. Générez-la avec `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`. |
| `DEBUG` | `True` en développement, `False` en production. |
| `ALLOWED_HOSTS` | Liste de domaines séparés par des virgules, autorisés à servir l'application. |
| `DATABASE_URL` | Chaîne de connexion PostgreSQL, au format `postgres://utilisateur:motdepasse@hote:port/nom_base`. |
| `CORS_ALLOWED_ORIGINS` | Origines autorisées à appeler l'API (URL du frontend Angular). |
| `EMAIL_BACKEND` | `django.core.mail.backends.console.EmailBackend` en développement, `django.core.mail.backends.smtp.EmailBackend` en production. |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Paramètres SMTP. Avec Gmail, `EMAIL_HOST_PASSWORD` doit être un mot de passe d'application, jamais le mot de passe du compte. |
| `DEFAULT_FROM_EMAIL` | Adresse d'expédition affichée sur les e-mails envoyés par la plateforme. |
| `FRONTEND_URL` | URL de base du frontend Angular, utilisée pour construire les liens envoyés par e-mail (activation de compte, réinitialisation de mot de passe). |
| `N8N_WEBHOOK_URL` | URL du webhook n8n déclenché à la création, la validation ou le refus d'une réservation. |

Une erreur fréquente : une clé copiée-collée avec un retour à la ligne accidentel dans `.env` reste silencieusement tronquée par le parseur d'environnement. En cas d'échec d'authentification inexpliqué entre ce service et le service IA, comparez la longueur exacte des deux valeurs plutôt que leur affichage dans un terminal.

## Lancement

```bash
python manage.py runserver
```

L'API est alors disponible sur `http://localhost:8000/api/`.

## Structure du projet

```
backend/
├── config/                 Réglages Django, routage racine
├── apps/
│   ├── utilisateurs/       Comptes, rôles, statut académique, activation par jeton
│   ├── laboratoires/       Laboratoires et leurs responsables
│   ├── equipements/        Équipements, catégories, alertes d'usure
│   ├── reservations/       Réservations, détection de conflit, résolution par priorité
│   ├── projets/            Projets de recherche et niveaux de priorité
│   ├── maintenances/       Cycle de vie des interventions (signalée, planifiée, en cours, terminée)
│   ├── consommables/       Stocks de réactifs, seuils d'alerte, mouvements
│   ├── notifications/      Notifications internes et envoi d'e-mails transactionnels
│   └── core/                Journal d'activité, endpoints transverses (recherche, rapports)
└── requirements.txt
```

Chaque application suit la même organisation interne : `models.py` porte les règles métier sous forme de méthodes du modèle plutôt que dans les vues, `serializers.py` définit la forme des données échangées, `views.py` expose les endpoints et applique les permissions.

## Modèle de rôles et règles métier

Quatre rôles existent : `ADMIN`, `TECHNICIEN`, `CHERCHEUR`, `ETUDIANT`.

La création d'une réservation applique les règles suivantes, dans cet ordre :

1. Un équipement marqué `necessite_validation` force le passage en attente de validation, quel que soit le rôle du demandeur.
2. Un demandeur de rôle `ETUDIANT` voit sa réservation placée en attente de validation.
3. Dans tous les autres cas, la réservation est confirmée immédiatement.

En cas de conflit de créneau sur un équipement, le système ne se contente pas de rejeter la demande : il compare la priorité du projet de recherche du demandeur (et son statut académique en cas d'égalité) à celle du demandeur déjà en attente sur le créneau, propose des créneaux alternatifs ou un équipement équivalent de même catégorie, et alerte les validateurs en cas de conflit de priorité. Une réservation déjà validée n'est en revanche jamais modifiée automatiquement : la décision finale reste toujours humaine.

## Authentification

L'authentification repose sur des jetons JWT (bibliothèque `djangorestframework-simplejwt`). Un compte créé par un administrateur n'a pas de mot de passe utilisable tant que son titulaire n'a pas cliqué sur le lien d'activation reçu par e-mail, valable trois jours et à usage unique. Les étudiants peuvent créer leur propre compte par inscription directe.

Le jeton d'accès embarque le rôle, le prénom et le nom de l'utilisateur, ce qui évite au service IA de rappeler ce backend uniquement pour connaître l'identité de la personne connectée.

## Documentation de l'API

La documentation interactive (Swagger, générée par `drf-spectacular`) est disponible à l'adresse `/api/docs/` une fois le serveur lancé. Le schéma OpenAPI brut est accessible sur `/api/schema/`.

## Automatisations n8n

Deux workflows n8n consomment cette API :

- **Rappels de réservation** : interroge `GET /api/reservations/a_rappeler_24h/` et `GET /api/reservations/a_rappeler_1h/` toutes les heures, envoie un e-mail de rappel, puis marque la réservation comme traitée via `POST /api/reservations/<id>/marquer_rappel_24h_envoye/` (ou son équivalent à une heure).
- **Notifications événementielles** : reçu sur `N8N_WEBHOOK_URL`, déclenché à la création, la validation ou le refus d'une réservation.

Ces workflows s'authentifient avec un jeton de longue durée dédié à un compte de service, distinct des jetons courts utilisés par les vrais utilisateurs, pour éviter toute expiration liée à la rotation des jetons de rafraîchissement.

## Tests

```bash
python manage.py test
```

## Déploiement

Le projet est prévu pour un déploiement sur Render : un service web pour cette API, une base PostgreSQL managée, et les variables d'environnement listées ci-dessus renseignées dans le tableau de bord Render plutôt que dans un fichier `.env` versionné.