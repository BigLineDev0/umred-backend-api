# SenLab — API métier (backend)

API REST de **SenLab**, plateforme web de gestion des réservations, des équipements, de la maintenance et des consommables des laboratoires de recherche universitaires.

Ce service porte toute la logique métier et la persistance des données. Il est consommé par deux clients :

- l'application **Angular** (`../frontend`), l'interface utilisateur ;
- le **service IA FastAPI** (`../umred-labo-ia`), l'assistant conversationnel. Il appelle cette API avec le jeton JWT de l'utilisateur connecté et n'a aucun accès direct à la base.

> SenLab a été conçu à partir du cas de l'**UMRED** (Unité Mixte de Recherche d'Exploration et de Diagnostic, UFR Santé, Université Iba Der Thiam de Thiès). Le produit est multi-établissement : chaque organisation cliente a ses données isolées et ses propres paramètres.

---

## Sommaire

1. [Architecture](#1-architecture)
2. [Stack technique](#2-stack-technique)
3. [Démarrage rapide](#3-démarrage-rapide)
4. [Variables d'environnement](#4-variables-denvironnement)
5. [Structure du projet](#5-structure-du-projet)
6. [Modèle de données](#6-modèle-de-données)
7. [Rôles et permissions](#7-rôles-et-permissions)
8. [Règles métier](#8-règles-métier)
9. [Multi-établissement](#9-multi-établissement)
10. [Authentification et sécurité](#10-authentification-et-sécurité)
11. [Référence de l'API](#11-référence-de-lapi)
12. [Tâches planifiées et automatisations](#12-tâches-planifiées-et-automatisations)
13. [Notifications et e-mails](#13-notifications-et-e-mails)
14. [Rapports, indicateurs et journal d'activité](#14-rapports-indicateurs-et-journal-dactivité)
15. [Fichiers envoyés et stockage](#15-fichiers-envoyés-et-stockage)
16. [Tests](#16-tests)
17. [Déploiement](#17-déploiement)
18. [Commandes de gestion](#18-commandes-de-gestion)
19. [Dépannage](#19-dépannage)

---

## 1. Architecture

```
                 ┌──────────────────────┐
  Navigateur ───▶│  Frontend Angular    │
                 └──────────┬───────────┘
                            │ REST + JWT                 ┌──────────────────────────┐
                            ├───────────────────────────▶│ Service IA (FastAPI)     │
                            ▼                            │ Qwen2.5-1.5B en local    │
                 ┌──────────────────────┐   REST + JWT   └────────────┬─────────────┘
                 │  API Django (ce dépôt)│◀────────────────────────────┘
                 │  règles métier, rôles │
                 └───┬──────────────┬───┘
                     │              │
              ┌──────▼─────┐  ┌─────▼──────────────────────────────┐
              │ PostgreSQL │  │ Planificateur (rappels, clôture)   │
              └────────────┘  │ n8n (synthèse hebdomadaire en PDF) │
                              └────────────────────────────────────┘
```

Principes :

- **Une seule source de vérité.** Disponibilités, conflits, permissions et statuts sont calculés ici. Le frontend et le service IA ne font qu'afficher ou relayer.
- **Les règles métier vivent dans les modèles.** On trouve par exemple `Reservation.creer()`, `valider()` et `annuler()`. Les vues se limitent aux permissions et à la sérialisation.
- **Isolation par établissement.** Toutes les requêtes sont filtrées sur l'organisation de l'utilisateur (voir [§9](#9-multi-établissement)).

## 2. Stack technique

| Composant | Version | Rôle |
|---|---|---|
| Python | 3.12 | Langage |
| Django | 5.2 LTS | Framework, ORM, migrations, administration |
| Django REST Framework | 3.17 | API REST, permissions, limitation de débit |
| djangorestframework-simplejwt | 5.5 | Authentification JWT (rotation et liste noire) |
| drf-spectacular | 0.30 | Schéma OpenAPI et interface Swagger |
| PostgreSQL | 16 (image Docker) | Base de données |
| Gunicorn | 23 | Serveur d'application en production |
| WhiteNoise | 6.12 | Fichiers statiques |
| django-storages + boto3 | 1.14 / 1.43 | Stockage S3 des fichiers envoyés (facultatif) |
| django-anymail | 15.2 | Envoi d'e-mails via Brevo (facultatif, sinon SMTP) |
| ReportLab | 5.0 | Rapports PDF |
| openpyxl | 3.1 | Exports Excel |
| django-cors-headers | 4.4 | CORS pour le frontend et le service IA |

Les tâches de fond passent par le planificateur et par n8n (voir [§12](#12-tâches-planifiées-et-automatisations)) : il n'y a pas de file de tâches de type Celery.

## 3. Démarrage rapide

### Option A — Toute la plateforme avec Docker Compose (recommandé)

Le fichier `docker-compose.yml` de ce dossier lance les cinq services : base de données, API, planificateur, frontend et service IA.

```bash
cd backend
cp .env.example .env                       # puis remplacer les valeurs « à-changer »
cp ../umred-labo-ia/.env.example ../umred-labo-ia/.env
docker compose up -d --build
docker compose exec backend python manage.py createsuperuser   # accès à /admin
docker compose exec backend python manage.py creer_super_admin --email vous@exemple.sn
```

| Service | Conteneur | Adresse locale |
|---|---|---|
| Frontend | `senlab_frontend` | http://localhost:8090 |
| API Django | `senlab_backend` | http://localhost:8010/api/ |
| Service IA | `senlab_ia` | http://localhost:8011/health |
| PostgreSQL | `senlab_db` | réseau Docker interne uniquement |
| Planificateur | `senlab_planificateur` | sans port |

Les ports ne sont publiés que sur `127.0.0.1`. En production, seul le Nginx du serveur est exposé (voir [§17](#17-déploiement)).

> Au premier lancement, le service IA télécharge le modèle (environ 3 Go). Il répond en mode dégradé, par règles, en attendant.

### Option B — API seule, sans Docker

Prérequis : Python 3.12 et un serveur PostgreSQL 14 ou plus.

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # renseigner DATABASE_URL, SECRET_KEY, DEBUG=True…
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver      # http://localhost:8000/api/
```

Pour un essai rapide sans PostgreSQL : `DATABASE_URL=sqlite:///db.sqlite3`. Le verrouillage des lignes utilisé à la création des réservations est alors moins strict que sous PostgreSQL : à réserver au développement.

### Premiers pas après l'installation

1. **Créer le super-administrateur** (éditeur de la plateforme) : `python manage.py creer_super_admin --email …`. Il reçoit un lien pour définir son mot de passe.
2. **Créer un établissement et son administrateur** depuis la console plateforme du frontend (`/plateforme`), connecté en super-administrateur.
3. **Configurer l'établissement.** L'administrateur de l'établissement crée laboratoires, équipements et comptes, puis règle horaires, couleurs et logo dans « Mon établissement ».

## 4. Variables d'environnement

Le fichier `.env` est lu au démarrage par `django-environ`. Ne le versionnez jamais : seul `.env.example` est suivi par Git.

### Django et base de données

| Variable | Défaut | Description |
|---|---|---|
| `SECRET_KEY` | *(obligatoire)* | Clé secrète Django. Elle **signe aussi les JWT** : la même valeur doit être donnée au service IA (`JWT_SECRET_KEY`). Pour la générer : `python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"`. |
| `DEBUG` | `False` | `True` uniquement en développement. |
| `ENABLE_API_DOCS` | `False` | Publie `/api/schema/` et `/api/docs/` (Swagger). |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Domaines autorisés, séparés par des virgules. |
| `DATABASE_URL` | `postgres://senlab:senlab@localhost:5432/senlab` | Chaîne de connexion. Docker Compose la remplace pour pointer vers le conteneur `db`. |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:4200` | Origines autorisées à appeler l'API (frontend). |
| `CSRF_TRUSTED_ORIGINS` | *(vide)* | Origines de confiance pour l'administration Django. |
| `SECURE_SSL_REDIRECT` | `True` | Redirection HTTPS. Mettre `False` derrière un proxy qui fait déjà la redirection. |
| `COOKIES_SECURISES` | `True` | Cookies de session et CSRF « Secure ». `False` en local (HTTP). |

### E-mails

| Variable | Défaut | Description |
|---|---|---|
| `EMAIL_BACKEND` | console | `django.core.mail.backends.smtp.EmailBackend` (SMTP) ou `anymail.backends.brevo.EmailBackend` (Brevo par API). |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | — | Paramètres SMTP. Avec Gmail, utilisez un mot de passe d'application. |
| `BREVO_API_KEY` | *(vide)* | Clé API Brevo, si vous utilisez le backend Anymail. |
| `DEFAULT_FROM_EMAIL` | `SenLab <no-reply@senlab.sn>` | Expéditeur affiché. |
| `FRONTEND_URL` | `http://localhost:4200` | Base des liens envoyés par e-mail (activation, mot de passe). |
| `CONTACT_EMAIL` | valeur de `EMAIL_HOST_USER` | Destinataire des messages du formulaire de contact. Vide : le formulaire répond 503. |

### Services et automatisations

| Variable | Défaut | Description |
|---|---|---|
| `TACHES_TOKEN` | *(vide)* | Secret des routes `/api/taches/…` appelées par n8n. La même valeur est transmise au service IA. Si la variable est vide, ces routes répondent 403. |
| `N8N_WEBHOOK_URL` | *(vide)* | Webhook n8n appelé à la création, la validation ou le refus d'une réservation. |

### Stockage des fichiers (facultatif)

| Variable | Description |
|---|---|
| `S3_BUCKET` | Active le stockage S3 dès qu'elle est renseignée (Supabase Storage, Cloudflare R2, AWS…). |
| `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION` | Connexion au stockage. |
| `S3_DOMAINE_PUBLIC` | Domaine public du bucket, pour des URL stables et non signées. |

### Variables lues par `docker-compose.yml`

| Variable | Description |
|---|---|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Identifiants du conteneur PostgreSQL. |
| `URL_API`, `URL_IA`, `URL_FRONTEND`, `HOTE_API` | URL publiques. Les valeurs par défaut sont celles de production (`senlab.site`). |
| `ORIGINES_FRONTEND`, `ORIGINES_FRONTEND_IA` | Origines CORS de l'API et du service IA. |

## 5. Structure du projet

```
backend/
├── config/
│   ├── settings.py           Réglages (lus depuis .env)
│   ├── urls.py               Routage racine, authentification, documentation
│   └── wsgi.py / asgi.py
├── apps/
│   ├── organisations/        Établissements, horaires par jour, isolation des données (isolation.py)
│   ├── utilisateurs/         Comptes, rôles, statut académique, encadrants, jetons d'activation
│   ├── laboratoires/         Laboratoires et leurs responsables
│   ├── equipements/          Équipements, alertes d'usure, indicateurs (services.py)
│   ├── reservations/         Réservations, conflits, file d'attente, alertes de créneau, rappels
│   ├── projets/              Projets de recherche et niveaux de priorité
│   ├── maintenances/         Cycle de vie des interventions
│   ├── consommables/         Stocks de réactifs, seuils, mouvements
│   ├── notifications/        Notifications internes, e-mails mis en page (emails.py, templates/)
│   └── core/                 Journal d'activité, recherche, rapports PDF/Excel, indicateurs,
│                             synthèse hebdomadaire, routes des tâches n8n
├── deploiement/
│   ├── nginx-senlab.conf     Nginx du serveur (HTTPS via Certbot)
│   └── n8n/                  Workflow n8n de la synthèse hebdomadaire
├── docker/entrypoint.sh      Attente de la base, migrations, collectstatic, Gunicorn
├── docker-compose.yml        Pile complète (5 services)
├── Dockerfile
└── requirements.txt
```

Chaque application suit la même organisation :

- `models.py` : données et règles métier ;
- `serializers.py` : format des échanges ;
- `views.py` : routes et permissions ;
- `tests*.py` : tests.

## 6. Modèle de données

| Entité | Rôle | Statuts / énumérations |
|---|---|---|
| `Organisation` | Établissement client, unité d'isolation. Identité visuelle et règles de réservation. | `est_active` (suspension) |
| `HoraireJour` | Horaire d'ouverture d'un établissement pour un jour de la semaine | `ferme` |
| `Utilisateur` | Compte rattaché à une organisation | `Role` : SUPER_ADMIN, ADMIN, TECHNICIEN, CHERCHEUR, ETUDIANT · `StatutCompte` : EN_ATTENTE, ACTIF, INACTIF · `StatutAcademique` : DOCTORANT, MAITRE_DE_CONFERENCES, PROFESSEUR |
| `JetonDefinitionMotDePasse` | Lien à usage unique | `MotifJeton` : INVITATION (3 jours), REINITIALISATION (1 h), VERIFICATION (24 h) |
| `Laboratoire` | Salle réservable, appartient à une organisation | DISPONIBLE, INDISPONIBLE |
| `Equipement` | Appareil d'un laboratoire. Manuel PDF, seuil d'heures avant maintenance, option « nécessite validation ». | DISPONIBLE, RESERVE, EN_MAINTENANCE, EN_PANNE, HORS_SERVICE |
| `Projet` | Projet de recherche servant d'arbitrage en cas de conflit | `NiveauPriorite` : BASSE, NORMALE, HAUTE, CRITIQUE |
| `Reservation` | Laboratoire + 0..n équipements sur un créneau | EN_ATTENTE, VALIDEE, REFUSEE, ANNULEE, TERMINEE |
| `AlerteCreneau` | Demande d'être prévenu si un créneau se libère | `active` |
| `Maintenance` | Intervention sur un équipement | `TypeMaintenance` : PREVENTIVE, CORRECTIVE · SIGNALEE, PLANIFIEE, EN_COURS, TERMINEE, ANNULEE |
| `Consommable` | Réactif ou consommable d'un laboratoire. Seuil d'alerte et date de péremption. | `UniteConsommable` : ML, L, G, KG, UNITE |
| `MouvementStock` | Entrée ou sortie de stock, tracée | UTILISATION, REAPPROVISIONNEMENT, AJUSTEMENT |
| `Notification` | Message interne (cloche du frontend) | RESERVATION, MAINTENANCE, VALIDATION, RAPPEL, SYSTEME |
| `JournalActivite` | Journal d'audit : auteur, action, objet concerné, horodatage | — |

## 7. Rôles et permissions

| Action | Étudiant | Membre du laboratoire (`CHERCHEUR`) | Technicien | Administrateur | Super-admin |
|---|:-:|:-:|:-:|:-:|:-:|
| S'inscrire seul | ✔ | — | — | — | — |
| Réserver | demande soumise à validation | ✔ direct, sauf exceptions (§8) | ✔ | ✔ | — |
| Valider ou refuser une demande | — | demandes de ses étudiants¹ | ✔ | ✔ | — |
| Équipements, consommables (création, modification) | — | consultation, déclaration d'utilisation | ✔ | ✔ | — |
| Laboratoires, comptes utilisateurs | — | — | — | ✔ | — |
| Maintenance (planifier, démarrer, clôturer) | signaler une panne | signaler une panne | ✔ | ✔ | — |
| Rapports, journal d'activité, paramètres de l'établissement | — | — | — | ✔ | — |
| Créer ou suspendre des établissements | — | — | — | — | ✔ |

¹ Sauf sur un équipement « nécessite validation » : seul un technicien ou un administrateur peut alors statuer.

Le super-administrateur pilote la plateforme, mais **ne voit pas les données métier** des établissements.

Autres règles :

- un administrateur ne peut ni modifier son propre rôle ni désactiver son propre compte ;
- un technicien n'agit (démarrer, clôturer, annuler) que sur ses interventions et sur les pannes non encore prises en charge. Le filtre `?equipement=` élargit uniquement la consultation de l'historique.

## 8. Règles métier

### Création d'une réservation (`Reservation.creer()`)

Contrôles exécutés dans cet ordre :

1. **Cohérence des champs** : horaires valides, créneau dans le futur.
2. **Règles de l'établissement** : jour ouvert, horaires d'ouverture, durée minimale et maximale, délai maximal de réservation à l'avance.
3. **Appartenance** : chaque équipement doit appartenir au laboratoire choisi.
4. **Disponibilité**, sous verrou transactionnel. `select_for_update()` verrouille les équipements dans un ordre fixe : deux demandes simultanées sur le même équipement ne peuvent pas être acceptées toutes les deux, et l'ordre fixe évite les interblocages.
5. **Conflit** avec une réservation déjà acquise : refus, avec message explicite.
6. **Maintenance planifiée** sur ce jour : refus.

Statut initial :

| Situation | Statut |
|---|---|
| Un équipement est marqué « nécessite validation » | `EN_ATTENTE`, quel que soit le rôle |
| Le demandeur est étudiant | `EN_ATTENTE`, validation par son encadrant ou un technicien |
| Une autre demande est déjà en attente sur ce créneau | `EN_ATTENTE`, arbitrage par un validateur |
| Sinon | `VALIDEE` immédiatement |

**Vérifier avant de réserver.** `POST /api/reservations/verifier/` exécute les mêmes contrôles sans rien enregistrer. Il renvoie le statut prévu, les conflits détaillés et des alternatives : créneaux libres sur 7 jours, ou équipements équivalents de la même catégorie. C'est l'écran de confirmation du frontend, et l'outil utilisé par l'assistant IA.

### File d'attente et priorité

Les demandes concurrentes en attente sont classées par :

1. **Niveau de priorité du projet** associé (CRITIQUE > HAUTE > NORMALE > BASSE ; NORMALE si aucun projet) ;
2. puis **statut académique** du demandeur ;
3. puis **ordre d'arrivée**.

Valider une demande refuse automatiquement les demandes concurrentes, qui sont notifiées. Une réservation déjà validée n'est **jamais** modifiée automatiquement : la décision finale reste humaine.

### Transitions

```
EN_ATTENTE ──▶ VALIDEE ──▶ TERMINEE  (automatique, une fois le créneau passé)
     │            │
     ├──▶ REFUSEE └──▶ ANNULEE
     └──▶ ANNULEE
```

Le validateur et la date de validation sont enregistrés. L'administrateur peut archiver les réservations terminées.

### Alertes de créneau

Un utilisateur peut demander à être prévenu si un créneau occupé se libère (`/api/reservations/alertes/`). À l'annulation, les personnes concernées reçoivent une notification et un e-mail, dans l'ordre de leurs demandes.

### Maintenance et alerte d'usure

- **Signalement.** Signaler une panne (`/api/maintenances/signaler_panne/`) crée une maintenance corrective et passe l'équipement `EN_PANNE`.
- **Clôture.** Clôturer remet l'équipement à disposition.
- **Alerte d'usure** (`apps/equipements/services.py`) :
  - **critique** : au moins 2 pannes en 90 jours, ou heures d'utilisation depuis la dernière maintenance ≥ 100 % du seuil de l'équipement ;
  - **attention** : heures ≥ 80 % du seuil, ou 1 panne récente.
- **Indicateurs** (`/api/equipements/{id}/statistiques/`) : taux d'occupation, MTBF, MTTR, score de santé.

L'alerte repose sur des seuils fixes : ce n'est pas un modèle prédictif.

### Consommables

- **Déclaration d'utilisation** (`retirer`) : refusée si le stock est insuffisant.
- **Réapprovisionnement** : réservé aux techniciens et aux administrateurs.
- **Traçabilité** : chaque mouvement est historisé avec son auteur.
- **Alertes** (`alertes_actives`) : stock sous le seuil, ou péremption à moins de 15 jours.

## 9. Multi-établissement

SenLab isole les données sur une base partagée (`apps/organisations/isolation.py`) :

- **En lecture**, `filtrer_par_organisation()` restreint chaque requête à l'organisation de l'utilisateur, en suivant les clés étrangères (par exemple réservation → laboratoire → organisation).
- **En écriture**, `ChampsOrganisationMixin` limite les clés étrangères acceptées par les serializers : impossible de rattacher une réservation à l'équipement d'un autre établissement en envoyant son identifiant.
- **Paramètres propres à chaque établissement** : nom, logo, couleurs, horaires par jour, durées minimale et maximale, délai maximal de réservation.
- **Suspension** : suspendre un établissement bloque immédiatement l'accès de ses utilisateurs, y compris les jetons déjà émis (voir [§10](#10-authentification-et-sécurité)).

## 10. Authentification et sécurité

- **Jetons JWT.** Accès valable 30 minutes. Rafraîchissement valable 7 jours, avec rotation et mise en liste noire de l'ancien jeton. La déconnexion (`/api/auth/logout/`) invalide le jeton de rafraîchissement.
- **Contenu du jeton** : `role`, `prenom`, `nom`, `organisation`. Le service IA identifie ainsi l'utilisateur sans rappeler l'API.
- **Contrôle à chaque requête.** La classe d'authentification `JWTAuthenticationEtablissement` refuse un jeton valide si le compte est désactivé ou si l'établissement est suspendu.
- **Activation des comptes.**
  - Un compte créé par un administrateur reçoit un lien d'invitation (3 jours, usage unique).
  - Un étudiant qui s'inscrit seul reçoit un lien de vérification (24 h) ; son compte reste bloqué jusqu'au clic.
  - Le lien de réinitialisation du mot de passe expire au bout d'une heure.
- **Limitation de débit** : 60 requêtes par minute pour les anonymes, 10 par minute sur les routes d'authentification (connexion, inscription, mot de passe oublié), 5 messages par heure sur le formulaire de contact.
- **Formulaire de contact** : champ piège invisible contre les robots ; le message part par e-mail avec l'adresse de l'expéditeur en réponse.
- **Mot de passe oublié.** La réponse est identique, que l'adresse existe ou non : pas de divulgation des comptes.
- **Journal d'activité.** Les actions sensibles y sont enregistrées : validations, exports de rapports, gestion des comptes, etc.

## 11. Référence de l'API

Toutes les routes sont préfixées par `/api/`. Sauf mention contraire, elles exigent un jeton (`Authorization: Bearer <access>`). Avec `ENABLE_API_DOCS=True`, la documentation interactive complète est disponible sur **`/api/docs/`**, et le schéma OpenAPI sur `/api/schema/`.

### Authentification et profil

| Méthode | Route | Description |
|---|---|---|
| POST | `auth/login/` | Connexion → `access`, `refresh`, profil (public) |
| POST | `auth/refresh/` | Nouveau jeton d'accès (public) |
| POST | `auth/logout/` | Déconnexion (liste noire du jeton de rafraîchissement) |
| POST | `auth/register/` | Inscription d'un étudiant dans un établissement actif (public) |
| POST | `auth/activer-compte/` · `auth/renvoyer-activation/` | Activation par lien e-mail (public) |
| GET | `auth/verifier-jeton/{jeton}/` | Validité d'un lien (public) |
| POST | `auth/definir-mot-de-passe/` · `auth/mot-de-passe-oublie/` | Définition et réinitialisation (public) |
| POST | `auth/changer-mot-de-passe/` | Changement de mot de passe connecté |
| GET/PATCH | `utilisateurs/moi/` | Mon profil |

### Ressources

Les routes ci-dessous s'ajoutent aux opérations CRUD standard (`GET` liste, `POST`, `GET/PUT/PATCH/DELETE {id}/`).

| Ressource | Route de base | Actions supplémentaires |
|---|---|---|
| Organisations | `organisations/` | `GET publiques/` (public, formulaire d'inscription) · `GET, PATCH courante/` (mon établissement) · `GET statistiques/` · `POST {id}/suspendre/` · `POST {id}/reactiver/` |
| Utilisateurs | `utilisateurs/` | `GET mes_etudiants/` · `GET encadrants/` · `POST assigner_encadrant/` · `POST {id}/activer/` · `POST {id}/desactiver/` |
| Laboratoires | `laboratoires/` | — |
| Équipements | `equipements/` | `GET {id}/alerte_usure/` · `GET {id}/statistiques/` · `GET alertes_usure_actives/` |
| Réservations | `reservations/` | `POST verifier/` · `GET file_attente/` · `GET creneaux_occupes/` · `GET, POST alertes/` · `POST supprimer_alerte/` · `POST {id}/valider/` · `POST {id}/refuser/` · `POST {id}/annuler/` · `POST {id}/archiver/` · `POST {id}/desarchiver/` · `GET {id}/historique/` |
| Projets | `projets/` | — |
| Maintenances | `maintenances/` | `POST signaler_panne/` · `POST {id}/prendre_en_charge/` · `POST {id}/demarrer/` · `POST {id}/cloturer/` · `POST {id}/annuler/` |
| Consommables | `consommables/` | `POST {id}/retirer/` · `POST {id}/reapprovisionner/` · `GET {id}/mouvements/` · `GET alertes_actives/` |
| Notifications | `notifications/` | `GET non_lues_count/` · `POST {id}/marquer_lue/` · `POST tout_marquer_lu/` |

Les réservations ne se modifient pas (pas de `PUT`/`PATCH`) : pour changer de créneau, on annule puis on refait une demande.

### Pilotage, rapports et recherche

| Méthode | Route | Description |
|---|---|---|
| POST | `contact/` | Formulaire de contact du site vitrine (public, 5 messages par heure et par IP) |
| GET | `recherche/?q=` | Recherche globale (équipements, laboratoires…) |
| GET | `mon-activite/` | Activité de l'utilisateur connecté |
| GET | `pilotage/indicateurs/` | Indicateurs d'activité (filtres : `date_debut`, `date_fin`, `laboratoire`) |
| GET | `rapports/export/` · `rapports/export-pdf/` | Rapport d'activité en Excel ou en PDF (administrateur) |
| GET | `logs/` | Journal d'activité (administrateur) |

### Routes des tâches planifiées

Ces routes ne sont pas liées à un utilisateur : elles exigent l'en-tête `X-Taches-Token` égal à `TACHES_TOKEN`.

| Méthode | Route | Description |
|---|---|---|
| POST | `taches/cloturer-reservations/` | Passe en `TERMINEE` les réservations dont le créneau est passé |
| POST | `taches/envoyer-rappels/` | Envoie les rappels 24 h et 1 h |
| GET | `taches/synthese-hebdomadaire/` | Établissements actifs, indicateurs des 7 derniers jours et destinataires |
| POST | `taches/synthese-hebdomadaire/rapport/` | Corps `{"organisation_id", "synthese"}` → rapport PDF, et notification des administrateurs |

### Exemple

```bash
# Connexion
curl -s -X POST http://localhost:8010/api/auth/login/ \
     -H "Content-Type: application/json" \
     -d '{"email": "tech@exemple.sn", "password": "…"}'

# Vérifier un créneau avant de réserver
curl -s -X POST http://localhost:8010/api/reservations/verifier/ \
     -H "Authorization: Bearer $ACCESS" -H "Content-Type: application/json" \
     -d '{"laboratoire": 1, "equipements": [3], "date": "2026-10-20",
          "heure_debut": "10:00", "heure_fin": "12:00", "motif": "Analyses ELISA"}'
```

## 12. Tâches planifiées et automatisations

| Tâche | Exécutée par | Fréquence |
|---|---|---|
| Rappels de réservation 24 h et 1 h avant le créneau (notification + e-mail) | Planificateur Django | toutes les 5 minutes |
| Clôture des réservations passées | Planificateur Django | toutes les 15 minutes |
| Synthèse hebdomadaire en PDF envoyée aux administrateurs | Workflow n8n | le lundi à 7 h (heure de Dakar) |
| Notifications liées aux événements de réservation | Webhook `N8N_WEBHOOK_URL` | à chaque création, validation ou refus |

### Le planificateur

Le planificateur est le conteneur `planificateur`, qui exécute `python manage.py planificateur`.

- **Rappels.** Les fenêtres de tolérance (23 à 25 h, 0 à 2 h) évitent de rater une réservation si une exécution est sautée.
- **Pas de double envoi.** Le drapeau `rappel_*_envoye` est posé par une mise à jour conditionnelle avant l'envoi : un rappel n'est jamais envoyé deux fois, même si deux exécutions se chevauchent.
- **Sans conteneur dédié** (Render, par exemple), un cron ou n8n peut appeler `POST /api/taches/envoyer-rappels/` et `POST /api/taches/cloturer-reservations/`.

### La synthèse hebdomadaire

Le workflow `deploiement/n8n/synthese-hebdomadaire.json` enchaîne quatre étapes :

1. `GET /api/taches/synthese-hebdomadaire/` : liste des établissements et de leurs indicateurs ;
2. `POST {IA}/api/pilotage/synthese/rediger` : le modèle Qwen rédige le texte. Tout chiffre absent des indicateurs fait rejeter le texte au profit de la version par gabarits ;
3. `POST /api/taches/synthese-hebdomadaire/rapport/` : rapport PDF, avec la synthèse en tête ;
4. envoi du PDF par e-mail aux administrateurs (nœud Gmail).

Mise en service dans n8n :

- **Adresses.** Le nœud « Configuration » contient `api_url` et `ia_url` : `http://localhost:8010/api` et `http://localhost:8011/api` en local (pile Docker), `https://api.senlab.site/api` et `https://ia.senlab.site/api` en ligne.
- **Identifiant « Header Auth »** (en-tête `X-Taches-Token`, valeur = `TACHES_TOKEN` du `.env` visé), à sélectionner dans les trois nœuds HTTP.
- **Identifiant « Gmail OAuth2 »** dans le nœud d'envoi.
- **Activation** du workflow. Le déclencheur ne s'exécute que si n8n tourne au moment prévu.

## 13. Notifications et e-mails

- `notifier()` (`apps/notifications/services.py`) crée la notification interne et, si demandé, envoie l'e-mail.
- **Envoi best-effort.** Une panne du serveur mail ne fait jamais échouer l'action métier : l'erreur est journalisée.
- **Mise en page.** Les e-mails ont une version HTML et une version texte (`templates/emails/`), aux couleurs de l'établissement du destinataire.
- **E-mails envoyés** : activation de compte, réinitialisation du mot de passe, rappels de réservation, créneau libéré, demande écartée, équipement devenu indisponible, etc.

## 14. Rapports, indicateurs et journal d'activité

- **Indicateurs** (`apps/core/analytique.py`) : volumes de réservations, taux d'annulation et de refus, délai moyen de validation, occupation des laboratoires et des équipements, heures de pointe, prévision de charge, recommandations. Une synthèse rédigée par des gabarits (`synthese_regles`) est toujours disponible.
- **Rapport PDF** (`apps/core/rapport_pdf.py`) : en-tête aux couleurs et au logo de l'établissement, indicateurs, recommandations, occupation, détail des réservations.
- **Export Excel** : données des réservations de la période.
- **Journal d'activité** : écrit uniquement par `journaliser()`, jamais modifiable par l'API, filtré par établissement.

## 15. Fichiers envoyés et stockage

Les photos de profil, logos d'établissement et manuels PDF des équipements sont servis sous `/media/` :

- **par défaut**, sur le disque, dans le volume Docker `media_data` ;
- **avec `S3_BUCKET` renseigné**, sur un stockage compatible S3. C'est indispensable sur un hébergement au disque éphémère.

## 16. Tests

```bash
python manage.py test                       # avec la base de .env (PostgreSQL)
DATABASE_URL=sqlite:///test.sqlite3 python manage.py test   # sans serveur PostgreSQL
python manage.py test apps.reservations     # une seule application
```

La suite compte **222 tests**. Elle couvre notamment :

- l'authentification, l'activation et la suspension ;
- les règles de création et de validation des réservations ;
- la file d'attente par priorité, la traçabilité et le refus des doublons d'une même demande ;
- les maintenances, les consommables et les e-mails ;
- les rappels et la synthèse hebdomadaire ;
- l'isolation entre établissements.

## 17. Déploiement

### Image Docker

Le `Dockerfile` construit une image Python 3.12 slim. Au démarrage, `docker/entrypoint.sh` effectue, dans l'ordre :

1. l'attente de PostgreSQL (30 essais, avec la cause exacte en cas d'échec) ;
2. `migrate` ;
3. `collectstatic` ;
4. le lancement de **Gunicorn** : 3 workers, délai de 60 s pour les exports PDF et Excel volumineux.

### Serveur VPS (configuration prévue)

1. Copier le dépôt sur le serveur, renseigner `backend/.env` et `umred-labo-ia/.env` avec les valeurs de production :
   - `DEBUG=False` ;
   - `COOKIES_SECURISES=True` ;
   - `SECRET_KEY` et `TACHES_TOKEN` forts.
2. `docker compose up -d --build` dans `backend/`.
3. Installer `deploiement/nginx-senlab.conf` (`senlab.site`, `api.senlab.site`, `ia.senlab.site`), puis `sudo certbot --nginx -d senlab.site -d api.senlab.site -d ia.senlab.site`.
4. Créer le super-administrateur : `docker compose exec backend python manage.py creer_super_admin --email …`.
5. Importer et activer le workflow n8n.

Dimensionnement : le modèle IA occupe environ 3 Go de RAM. Prévoyez un serveur d'au moins 8 Go (16 Go conseillés) pour l'ensemble de la pile.

### Liste de contrôle de production

- [ ] `DEBUG=False`, `ENABLE_API_DOCS=False` (sauf besoin ponctuel).
- [ ] `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS` et `CSRF_TRUSTED_ORIGINS` limités aux domaines réels.
- [ ] HTTPS actif et `COOKIES_SECURISES=True`.
- [ ] Sauvegardes de PostgreSQL et du volume `media_data`.
- [ ] Expéditeur d'e-mails configuré et domaine authentifié (SPF, DKIM).

## 18. Commandes de gestion

| Commande | Description |
|---|---|
| `python manage.py planificateur` | Boucle des tâches planifiées (rappels toutes les 5 min, clôture toutes les 15 min) |
| `python manage.py envoyer_rappels_reservations` | Envoie une fois les rappels dus |
| `python manage.py cloturer_reservations` | Clôture une fois les réservations passées |
| `python manage.py creer_super_admin --email … [--prenom … --nom …]` | Crée le super-administrateur et lui envoie un lien de définition du mot de passe |
| `python manage.py createsuperuser` | Compte d'accès à l'administration Django (`/admin/`) |

## 19. Dépannage

| Symptôme | Cause probable | Solution |
|---|---|---|
| L'assistant répond « session expirée » alors que l'utilisateur est connecté | `SECRET_KEY` (Django) ≠ `JWT_SECRET_KEY` (service IA) | Utiliser la même valeur. Attention aux retours à la ligne copiés dans `.env` : comparez la longueur des deux valeurs. |
| Les liens des e-mails pointent vers `localhost:4200` | `FRONTEND_URL` / `URL_FRONTEND` non renseignées | Mettre l'URL réelle du frontend |
| Erreur 403 sur `/api/taches/…` | `TACHES_TOKEN` vide ou différent | Renseigner le même secret dans `.env` et dans l'identifiant n8n |
| Aucun e-mail reçu en local | `EMAIL_BACKEND` = console | Les e-mails s'affichent dans les journaux : `docker compose logs backend` |
| Logos et manuels introuvables après un redéploiement | Disque éphémère | Configurer `S3_BUCKET` ou monter un volume |
| Le conteneur backend redémarre en boucle | Base injoignable | Lire la cause dans `docker compose logs backend` (mot de passe, hôte) |
