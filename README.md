# Stalwart Signup Manager for Bulwark

A reusable, self-hosted signup and mailbox-approval workflow for Stalwart Mail Server and Bulwark Webmail.

This project ships two components:

1. A companion service that hosts a public signup form, encrypts pending passwords, creates Stalwart accounts through JMAP, and optionally sends administrator email notifications.
2. A native Bulwark plugin that adds a Stalwart Signup Manager page to the Bulwark admin console. Administrators can review requests, approve or reject them, and switch between manual and automatic approval.

No Bulwark source fork or modification is needed.

## Compatibility

- Bulwark Webmail 1.20.0 or later, with the full plugin system and admin console enabled.
- Stalwart with JMAP account management enabled and an administrator account allowed to create users.
- A public HTTPS URL for the companion service.
- Docker Engine and Docker Compose for the easiest companion-service deployment.

Bulwark Lite does not provide the plugin/admin-console extension points required by this integration.

## How it works

1. A visitor requests an email address through the public signup form.
2. The companion validates the requested address, checks that it does not already exist in Stalwart, and encrypts the submitted password before saving a pending request.
3. In manual mode, the request appears in the Bulwark admin plugin page. An email notification is also sent when SMTP is configured.
4. The administrator approves or rejects the request from Bulwark. Approval creates the mailbox through Stalwart JMAP.
5. In automatic mode, new requests are created immediately. If automatic creation fails, the request remains pending for manual review.

The Stalwart administrator password and the encryption key remain on the companion-service host. The Bulwark plugin uses an admin-scope credential, so the API token is injected by Bulwark's server-side proxy and is not exposed to the plugin UI.

## 1. Configure the companion service

Copy the example environment file:

    cp .env.example .env

Generate two independent secrets:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    openssl rand -hex 32

Set the generated values in .env:

- FIELD_ENCRYPTION_KEY: the Fernet key from the Python command.
- ADMIN_API_TOKEN: the random token from OpenSSL.
- STALWART_URL: Stalwart's HTTPS base URL, without a trailing slash.
- STALWART_ADMIN_USER and STALWART_ADMIN_PASSWORD: a Stalwart administrator account.
- STALWART_DOMAINS_JSON: a JSON object mapping each allowed email domain to its Stalwart domain ID.
- PUBLIC_BASE_URL: the public HTTPS URL where the signup form will be available.
- DEFAULT_APPROVAL_MODE: manual or auto.

Example domain mapping:

    {"example.net":"STALWART_DOMAIN_ID"}

Do not copy the sample domain ID; use the ID for the domain on your own Stalwart server. Configure every domain that the public form should allow.

Optional email notifications:

- SMTP_HOST
- SMTP_PORT (465 for implicit TLS; other ports use STARTTLS)
- SMTP_USERNAME
- SMTP_PASSWORD
- SMTP_FROM
- ADMIN_NOTIFICATION_EMAIL

Leave SMTP_HOST empty to disable email notifications. Approval and rejection remain available in the Bulwark plugin.

Start the service:

    docker compose up -d --build

The companion listens on 127.0.0.1:8088 on the Docker host by default. Configure your HTTPS reverse proxy to forward a hostname such as signup.example.net to that address. Do not expose the raw service port directly to the public Internet.

The public form is served at /. Health checks use /healthz.

## 2. Build and install the Bulwark plugin

From the repository root:

    npm install
    npm run build:plugin

The installable archive is created at:

    plugin/stalwart-signup-manager.zip

In Bulwark, sign in as an administrator and open Admin Console → Plugins. Upload the ZIP, then enable Stalwart Signup Manager for the instance.

Open the plugin's admin page and configure:

- Signup service URL: the HTTPS origin of the companion service, for example https://signup.example.net.
- Admin API token: the value of ADMIN_API_TOKEN from the companion service's .env file.

Save the configuration and reload the plugin page. Pending requests and the approval-mode control will appear there.

If the companion service is on a private network, Bulwark's server-side plugin proxy must be allowed to reach its HTTPS origin. Configure PLUGIN_PROXY_PRIVATE_ORIGINS on the Bulwark server if your deployment requires it. The token is stored as an admin-scope credential and is never placed in plugin source code or browser settings.

## 3. Manual and automatic approval

The initial mode is read from DEFAULT_APPROVAL_MODE. The administrator can change it in the plugin page:

- Manual: new requests wait for an administrator to approve or reject them.
- Automatic: new requests are created in Stalwart immediately. Failed automatic attempts remain pending so an administrator can retry or reject them.

Changing the mode affects new requests only. Existing pending requests still need an explicit action.

## Security and operations

- Pending passwords are encrypted at rest with Fernet. The password is never returned by the admin API and is never included in notification emails.
- The Fernet key must be backed up securely with the database. Losing the key makes existing pending passwords unrecoverable.
- The companion admin API requires a bearer token of at least 32 characters.
- The Bulwark plugin uses Bulwark's server-side credential proxy. The API token is not exposed to the plugin frame.
- Approve/reject actions are performed by the companion service. The plugin never receives the submitted password.
- Public signup is rate-limited per client IP.
- Pending requests older than 14 days expire and their encrypted passwords are cleared.
- Back up the Docker volume signup_data and FIELD_ENCRYPTION_KEY together.
- Restrict access to .env, database backups and the Docker host. Never commit production secrets.

The companion uses Stalwart's JMAP account extension methods x:Account/get and x:Account/set. It does not require changes to the Stalwart or Bulwark source code.

## Development and tests

Backend tests:

    pip install -r requirements-dev.txt
    python -m unittest discover -s tests -v

Plugin tests and packaging:

    npm install
    npm test
    npm run build:plugin

The GitHub Actions workflow runs both test suites and publishes the plugin ZIP as a build artifact.

## Companion admin API

All /api/v1/admin endpoints require:

    Authorization: Bearer ADMIN_API_TOKEN

Endpoints:

- GET /api/v1/admin/settings — read approval mode and notification status.
- PUT /api/v1/admin/settings — set approvalMode to manual or auto.
- GET /api/v1/admin/requests — list pending requests, without passwords.
- POST /api/v1/admin/requests/{id}/approve — create the Stalwart mailbox.
- POST /api/v1/admin/requests/{id}/reject — reject the request and erase its encrypted password.
