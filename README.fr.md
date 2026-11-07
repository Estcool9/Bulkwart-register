# Stalwart Signup Manager pour Bulwark

Intégration réutilisable pour Stalwart et Bulwark Webmail.

Le projet comprend un service compagnon indépendant qui héberge le formulaire public, chiffre les mots de passe en attente et crée les boîtes via JMAP, ainsi qu'un plugin Bulwark qui ajoute une page de gestion dans la console d'administration.

Aucune modification du code source de Bulwark n'est nécessaire.

## Prérequis

- Bulwark Webmail 1.20.0 ou plus récent, édition complète avec les plugins activés.
- Stalwart avec l'API JMAP et la gestion des comptes activées.
- Docker et Docker Compose.
- Une URL HTTPS publique pour le service d'inscription.

Bulwark Lite ne fournit pas l'interface de plugins nécessaire.

## Raccorder un portail d'inscription existant

Le plugin peut aussi gérer un portail FastAPI déjà installé. Conserve son formulaire public, sa base SQLite, sa clé de chiffrement, ses identifiants Stalwart et sa fonction de création de boîtes ; ajoute uniquement l'API d'administration protégée décrite dans integration/existing-portal/README.md. Cela évite une deuxième base et garde toutes les demandes dans la même file.

Dans les paramètres du plugin, indique l'origine HTTPS du portail dans Signup service URL et son préfixe public (par exemple /signup) dans Signup service path. Utilise / si l'API est hébergée à la racine. Le service Docker autonome ci-dessous reste disponible pour une nouvelle installation ; il n'est pas nécessaire pour un portail existant.

## Installation rapide

1. Copier .env.example vers .env.
2. Générer une clé Fernet avec la commande Python indiquée dans README.md.
3. Générer un jeton API avec openssl rand -hex 32.
4. Renseigner les identifiants Stalwart, les domaines autorisés et leurs IDs Stalwart dans STALWART_DOMAINS_JSON.
5. Définir FIELD_ENCRYPTION_KEY, ADMIN_API_TOKEN, PUBLIC_BASE_URL et DEFAULT_APPROVAL_MODE.
6. Démarrer le service avec docker compose up -d --build.
7. Placer le service derrière un reverse proxy HTTPS. Le port hôte 8088 est limité à localhost par défaut.
8. Construire le plugin depuis la racine du dépôt avec npm install puis npm run build:plugin.
9. Dans Bulwark, ouvrir Admin Console → Plugins, téléverser plugin/stalwart-signup-manager.zip et activer le plugin.
10. Dans sa page d'administration, renseigner l'URL HTTPS du service et le jeton ADMIN_API_TOKEN.

## Modes d'approbation

- Manuel : chaque demande attend l'approbation ou le refus de l'administrateur.
- Automatique : les nouvelles demandes créent immédiatement une boîte. En cas d'échec, la demande reste en attente pour une vérification manuelle.

Le mode se change dans la page du plugin. Les demandes déjà en attente ne sont pas approuvées automatiquement lors d'un changement de mode.

## Sécurité

Les mots de passe en attente sont chiffrés avec Fernet. La clé de chiffrement et la base SQLite doivent être sauvegardées ensemble. Le mot de passe administrateur Stalwart et le jeton d'API restent sur le serveur compagnon. Le plugin utilise le proxy de credentials côté serveur de Bulwark ; il ne reçoit jamais le mot de passe du demandeur.

La configuration SMTP est facultative. Sans SMTP, les demandes restent consultables et traitables depuis la page du plugin.

Voir README.md pour les détails, la configuration SMTP, les tests et les endpoints.
