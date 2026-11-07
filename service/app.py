import html
import hmac
import json
import logging
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Literal

import requests
from cryptography.fernet import Fernet
from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("stalwart-signup")


def read_secret(name: str) -> str:
    file_path = os.environ.get(name + "_FILE")
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError("Required environment variable " + name + " is missing.")
    return value


APP_NAME = os.environ.get("APP_NAME", "Mail Signup")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
ADMIN_CONSOLE_URL = os.environ.get("ADMIN_CONSOLE_URL", "").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/requests.sqlite3")
DEFAULT_APPROVAL_MODE = os.environ.get("DEFAULT_APPROVAL_MODE", "manual").lower()
if DEFAULT_APPROVAL_MODE not in {"manual", "auto"}:
    raise RuntimeError("DEFAULT_APPROVAL_MODE must be 'manual' or 'auto'.")

STALWART_URL = os.environ.get("STALWART_URL", "").rstrip("/")
STALWART_ADMIN_USER = os.environ.get("STALWART_ADMIN_USER", "")
STALWART_ADMIN_PASSWORD = read_secret("STALWART_ADMIN_PASSWORD")
ADMIN_API_TOKEN = read_secret("ADMIN_API_TOKEN")
FIELD_ENCRYPTION_KEY = read_secret("FIELD_ENCRYPTION_KEY")
FERNET = Fernet(FIELD_ENCRYPTION_KEY.encode("ascii"))

try:
    DOMAIN_MAP = json.loads(os.environ.get("STALWART_DOMAINS_JSON", "{}"))
except json.JSONDecodeError as exc:
    raise RuntimeError("STALWART_DOMAINS_JSON must be a JSON object.") from exc

if not isinstance(DOMAIN_MAP, dict) or not DOMAIN_MAP:
    raise RuntimeError("STALWART_DOMAINS_JSON must contain at least one permitted domain.")

DOMAIN_MAP = {
    str(domain).strip().lower(): str(domain_id).strip()
    for domain, domain_id in DOMAIN_MAP.items()
}
if any(
    not re.fullmatch(r"[a-z0-9.-]+", domain) or not domain_id
    for domain, domain_id in DOMAIN_MAP.items()
):
    raise RuntimeError("STALWART_DOMAINS_JSON contains an invalid domain or empty domain ID.")

if not STALWART_URL.startswith("https://") or not STALWART_ADMIN_USER:
    raise RuntimeError("STALWART_URL must be HTTPS and STALWART_ADMIN_USER must be set.")
if len(ADMIN_API_TOKEN) < 32:
    raise RuntimeError("ADMIN_API_TOKEN must contain at least 32 characters.")
if len(FIELD_ENCRYPTION_KEY) < 40:
    raise RuntimeError("FIELD_ENCRYPTION_KEY does not look like a Fernet key.")
if not PUBLIC_BASE_URL.startswith("https://"):
    raise RuntimeError("PUBLIC_BASE_URL must be set to an HTTPS URL.")

LOCAL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,30}[a-z0-9])?$")
RESERVED = {
    "admin", "administrator", "root", "postmaster", "abuse", "hostmaster",
    "webmaster", "mailer-daemon", "security", "noreply", "no-reply",
}
PASSWORD_MIN = 12
PASSWORD_MAX = 128
RATE_LIMIT_PER_HOUR = 5
PENDING_TTL_SECONDS = 14 * 86400

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA secure_delete=ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            localpart TEXT NOT NULL,
            domain TEXT NOT NULL,
            domain_id TEXT NOT NULL,
            encrypted_password TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            client_ip TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            reviewed_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS requests_status_time ON requests(status, created_at);
        CREATE TABLE IF NOT EXISTS rate_events (
            client_ip TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS rate_events_ip_time ON rate_events(client_ip, created_at);
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES('approval_mode', ?)",
        (DEFAULT_APPROVAL_MODE,),
    )
    conn.execute(
        "UPDATE requests SET status='expired', encrypted_password=NULL, reviewed_at=? "
        "WHERE status='pending' AND created_at<?",
        (int(time.time()), int(time.time()) - PENDING_TTL_SECONDS),
    )
    conn.commit()
    return conn


def get_approval_mode() -> str:
    conn = db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key='approval_mode'").fetchone()
        return row["value"] if row else DEFAULT_APPROVAL_MODE
    finally:
        conn.close()


def set_approval_mode(mode: str) -> None:
    if mode not in {"manual", "auto"}:
        raise ValueError("Approval mode must be manual or auto.")
    conn = db()
    try:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES('approval_mode', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (mode,),
        )
        conn.commit()
    finally:
        conn.close()


def require_admin(authorization: str | None) -> None:
    expected = "Bearer " + ADMIN_API_TOKEN
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="A valid admin API bearer token is required.")


def jmap_call(method: str, arguments: dict) -> dict:
    session_response = requests.get(
        STALWART_URL + "/jmap/session",
        auth=(STALWART_ADMIN_USER, STALWART_ADMIN_PASSWORD),
        timeout=(5, 15),
    )
    session_response.raise_for_status()
    session = session_response.json()
    account_id = session.get("primaryAccounts", {}).get("urn:ietf:params:jmap:core")
    if not account_id:
        account_id = next(iter(session.get("accounts", {})), None)
    if not account_id:
        raise RuntimeError("Stalwart did not expose a JMAP administrator account.")

    payload = {
        "using": ["urn:ietf:params:jmap:core", "urn:stalwart:jmap"],
        "methodCalls": [[method, {"accountId": account_id, **arguments}, "signup"]],
    }
    response = requests.post(
        session["apiUrl"],
        json=payload,
        auth=(STALWART_ADMIN_USER, STALWART_ADMIN_PASSWORD),
        timeout=(5, 20),
    )
    response.raise_for_status()
    data = response.json()
    for name, result, tag in data.get("methodResponses", []):
        if tag == "signup":
            if name == "error":
                raise RuntimeError(
                    result.get("description") or result.get("type") or "Stalwart JMAP error."
                )
            return result
    raise RuntimeError("Stalwart returned an incomplete JMAP response.")


def existing_addresses() -> set[str]:
    result = jmap_call("x:Account/get", {})
    return {
        str(item.get("emailAddress", "")).strip().lower()
        for item in result.get("list", [])
        if item.get("emailAddress")
    }


def create_mailbox(row: sqlite3.Row) -> str:
    email_address = (row["localpart"] + "@" + row["domain"]).lower()
    if email_address in existing_addresses():
        raise RuntimeError("This address already exists in Stalwart.")

    encrypted = row["encrypted_password"]
    if not encrypted:
        raise RuntimeError("The encrypted password is no longer available.")
    password = FERNET.decrypt(encrypted.encode("ascii")).decode("utf-8")

    result = jmap_call(
        "x:Account/set",
        {
            "create": {
                "signup": {
                    "@type": "User",
                    "name": row["localpart"],
                    "domainId": row["domain_id"],
                    "credentials": {
                        "0": {"@type": "Password", "secret": password}
                    },
                    "aliases": {},
                    "memberGroupIds": {},
                    "roles": {"@type": "User"},
                    "permissions": {"@type": "Inherit"},
                    "quotas": {},
                    "encryptionAtRest": {"@type": "Disabled"},
                }
            }
        },
    )
    created = result.get("created", {}).get("signup")
    if not created or not created.get("id"):
        raise RuntimeError("Stalwart did not confirm mailbox creation.")
    return email_address


def approve_request(request_id: int) -> str:
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Request not found.")
        if row["status"] == "approved":
            return row["localpart"] + "@" + row["domain"]
        if row["status"] != "pending":
            raise HTTPException(status_code=409, detail="This request is no longer pending.")
        changed = conn.execute(
            "UPDATE requests SET status='processing' WHERE id=? AND status='pending'",
            (request_id,),
        ).rowcount
        if changed != 1:
            raise HTTPException(status_code=409, detail="This request is already being processed.")
        conn.commit()
    finally:
        conn.close()

    email_address = row["localpart"] + "@" + row["domain"]
    try:
        created_address = create_mailbox(row)
    except Exception as exc:
        # A network timeout can happen after Stalwart has already created the
        # account. Reconcile before returning the request to pending.
        already_created = False
        try:
            already_created = email_address.lower() in existing_addresses()
        except Exception:
            log.exception("Could not reconcile Stalwart state after an approval error.")
        conn = db()
        try:
            if already_created:
                conn.execute(
                    "UPDATE requests SET status='approved', encrypted_password=NULL, reviewed_at=? "
                    "WHERE id=? AND status='processing'",
                    (int(time.time()), request_id),
                )
                conn.commit()
                return email_address
            conn.execute(
                "UPDATE requests SET status='pending' WHERE id=? AND status='processing'",
                (request_id,),
            )
            conn.commit()
        finally:
            conn.close()
        raise HTTPException(status_code=502, detail=str(exc)[:240]) from exc

    conn = db()
    try:
        conn.execute(
            "UPDATE requests SET status='approved', encrypted_password=NULL, reviewed_at=? "
            "WHERE id=? AND status='processing'",
            (int(time.time()), request_id),
        )
        conn.commit()
    finally:
        conn.close()
    return created_address


def send_admin_notification(request_id: int, email_address: str, status: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    admin_email = os.environ.get("ADMIN_NOTIFICATION_EMAIL", "").strip()
    if not smtp_host or not admin_email:
        return

    message = EmailMessage()
    message["Subject"] = "Signup request " + status + ": " + email_address + " (#" + str(request_id) + ")"
    message["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USERNAME") or admin_email
    message["To"] = admin_email
    admin_link = ADMIN_CONSOLE_URL or "Bulwark Admin Console > Plugins > Stalwart Signup Manager"
    message.set_content(
        "Address: " + email_address + "\n"
        + "Request: #" + str(request_id) + "\n"
        + "Status: " + status + "\n\n"
        + "Review in: " + admin_link + "\n\n"
        + "The submitted password is not included in this email.\n"
    )

    port = int(os.environ.get("SMTP_PORT", "465"))
    context = ssl.create_default_context()
    username = os.environ.get("SMTP_USERNAME", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    if port == 465:
        with smtplib.SMTP_SSL(smtp_host, port, timeout=15, context=context) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(smtp_host, port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)


def shell(title: str, body: str) -> HTMLResponse:
    page = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>""" + html.escape(title) + " · " + html.escape(APP_NAME) + """</title>
<style>
:root{color-scheme:light dark}*{box-sizing:border-box}
body{margin:0;background:#101827;color:#e5edf7;font:16px system-ui,sans-serif}
main{max-width:600px;margin:6vh auto;padding:20px}
.panel{background:#1a2638;border:1px solid #34445b;border-radius:16px;padding:28px}
h1{font-size:1.55rem;margin:0 0 10px}p{color:#b7c5d8;line-height:1.5}
label{display:block;margin:15px 0 6px}
input,select{width:100%;padding:12px;border-radius:9px;border:1px solid #52657f;background:#0e1725;color:inherit;font:inherit}
button,.button{display:inline-block;margin-top:18px;padding:11px 18px;border:0;border-radius:9px;background:#3979cf;color:white;font:inherit;font-weight:650;text-decoration:none;cursor:pointer}
.muted{font-size:.9rem;color:#95a8c0}.success{background:#203f3a;border:1px solid #4b8878;padding:12px;border-radius:9px}
.error{background:#482c34;border:1px solid #a45c69;padding:12px;border-radius:9px}a{color:#9ec5ff}
</style>
</head>
<body><main><section class="panel"><h1>""" + html.escape(title) + "</h1>" + body + """</section></main></body></html>"""
    return HTMLResponse(
        page,
        headers={
            "Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Cache-Control": "no-store",
        },
    )


@app.get("/healthz")
def healthz() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/")
def signup_form() -> HTMLResponse:
    token = secrets.token_urlsafe(32)
    secure_cookie = PUBLIC_BASE_URL.startswith("https://")
    options = "".join(
        "<option value='" + html.escape(domain) + "'>@" + html.escape(domain) + "</option>"
        for domain in sorted(DOMAIN_MAP)
    )
    body = (
        "<p>Request a mailbox. The administrator may review the request before an account is created.</p>"
        "<form method='post' action='/submit'>"
        "<input type='hidden' name='csrf_token' value='" + html.escape(token) + "'>"
        "<label>Email username</label>"
        "<input name='localpart' required minlength='2' maxlength='32' autocomplete='username' placeholder='first.last'>"
        "<label>Email domain</label><select name='domain' required>" + options + "</select>"
        "<label>Password (at least " + str(PASSWORD_MIN) + " characters)</label>"
        "<input type='password' name='password' required minlength='" + str(PASSWORD_MIN) + "' maxlength='" + str(PASSWORD_MAX) + "' autocomplete='new-password'>"
        "<label>Confirm password</label>"
        "<input type='password' name='password_confirm' required minlength='" + str(PASSWORD_MIN) + "' maxlength='" + str(PASSWORD_MAX) + "' autocomplete='new-password'>"
        "<button type='submit'>Request account</button></form>"
        "<p class='muted'>Do not reuse a password from another service.</p>"
    )
    response = shell("Request an email address", body)
    response.set_cookie(
        "signup_csrf",
        token,
        httponly=True,
        secure=secure_cookie,
        samesite="strict",
        max_age=1800,
        path="/",
    )
    return response


@app.post("/submit")
def submit_request(
    request: Request,
    csrf_token: str = Form(...),
    localpart: str = Form(...),
    domain: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
) -> HTMLResponse:
    cookie_token = request.cookies.get("signup_csrf", "")
    if not cookie_token or not hmac.compare_digest(cookie_token, csrf_token):
        return shell("Request rejected", "<p class='error'>Your form expired. Reload the signup page and try again.</p>")

    local = localpart.strip().lower()
    domain = domain.strip().lower()
    if domain not in DOMAIN_MAP or len(local) < 2 or not LOCAL_RE.fullmatch(local):
        return shell("Invalid request", "<p class='error'>The username or email domain is not allowed.</p>")
    if local in RESERVED or ".." in local:
        return shell("Invalid request", "<p class='error'>This username is reserved or unavailable.</p>")
    if len(password) < PASSWORD_MIN or len(password) > PASSWORD_MAX or password != password_confirm:
        return shell(
            "Invalid request",
            "<p class='error'>Passwords must match and contain between "
            + str(PASSWORD_MIN) + " and " + str(PASSWORD_MAX) + " characters.</p>",
        )

    client_ip = (request.client.host if request.client else "unknown")[:64]
    now = int(time.time())
    conn = db()
    try:
        recent = conn.execute(
            "SELECT COUNT(*) FROM rate_events WHERE client_ip=? AND created_at>?",
            (client_ip, now - 3600),
        ).fetchone()[0]
        if recent >= RATE_LIMIT_PER_HOUR:
            return shell("Request limit reached", "<p class='error'>Too many requests. Please try again later.</p>")
        conn.execute("INSERT INTO rate_events(client_ip, created_at) VALUES(?,?)", (client_ip, now))
        conn.execute("DELETE FROM rate_events WHERE created_at<?", (now - 86400,))
        email_address = local + "@" + domain
        duplicate_pending = conn.execute(
            "SELECT 1 FROM requests WHERE localpart=? AND domain=? AND status IN ('pending','processing')",
            (local, domain),
        ).fetchone()
        if duplicate_pending:
            return shell("Request already received", "<p class='success'>A request for this address is already being reviewed.</p>")
        conn.commit()
    finally:
        conn.close()

    try:
        if email_address in existing_addresses():
            return shell("Address unavailable", "<p class='error'>This address already exists or is unavailable.</p>")
    except Exception:
        log.exception("Could not check Stalwart address availability")
        return shell(
            "Service temporarily unavailable",
            "<p class='error'>We could not check address availability. Please try again later.</p>",
        )

    encrypted_password = FERNET.encrypt(password.encode("utf-8")).decode("ascii")
    conn = db()
    try:
        conn.execute(
            "INSERT INTO requests(localpart, domain, domain_id, encrypted_password, status, client_ip, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (local, domain, DOMAIN_MAP[domain], encrypted_password, "pending", client_ip, now),
        )
        request_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    status = "pending"
    if get_approval_mode() == "auto":
        try:
            approve_request(request_id)
            status = "approved"
        except Exception:
            log.exception("Automatic approval failed for request #%s", request_id)
            status = "pending"

    try:
        send_admin_notification(request_id, email_address, status)
    except Exception:
        log.exception("Could not send admin notification for request #%s", request_id)

    if status == "approved":
        body = (
            "<p class='success'>Your mailbox <strong>" + html.escape(email_address)
            + "</strong> has been created (request #" + str(request_id) + "). You can now sign in.</p>"
        )
        return shell("Mailbox created", body)

    body = (
        "<p class='success'>Your request for <strong>" + html.escape(email_address)
        + "</strong> has been submitted (reference #" + str(request_id)
        + "). It is waiting for administrator review. The mailbox has not yet been created.</p>"
    )
    return shell("Request submitted", body)


class ApprovalSettings(BaseModel):
    approvalMode: Literal["manual", "auto"]


@app.get("/api/v1/admin/settings")
def admin_get_settings(authorization: str | None = Header(default=None)) -> dict:
    require_admin(authorization)
    return {
        "approvalMode": get_approval_mode(),
        "notificationsEnabled": bool(
            os.environ.get("SMTP_HOST", "").strip()
            and os.environ.get("ADMIN_NOTIFICATION_EMAIL", "").strip()
        ),
        "notificationEmail": os.environ.get("ADMIN_NOTIFICATION_EMAIL", "").strip() or None,
    }


@app.put("/api/v1/admin/settings")
def admin_update_settings(
    settings: ApprovalSettings,
    authorization: str | None = Header(default=None),
) -> dict:
    require_admin(authorization)
    set_approval_mode(settings.approvalMode)
    return {"ok": True, "approvalMode": settings.approvalMode}


@app.get("/api/v1/admin/requests")
def admin_list_requests(authorization: str | None = Header(default=None)) -> dict:
    require_admin(authorization)
    conn = db()
    try:
        rows = conn.execute(
            "SELECT id, localpart, domain, created_at FROM requests "
            "WHERE status='pending' ORDER BY created_at ASC"
        ).fetchall()
        return {
            "requests": [
                {
                    "id": row["id"],
                    "address": row["localpart"] + "@" + row["domain"],
                    "createdAt": row["created_at"],
                }
                for row in rows
            ]
        }
    finally:
        conn.close()


@app.post("/api/v1/admin/requests/{request_id}/approve")
def admin_approve_request(
    request_id: int,
    authorization: str | None = Header(default=None),
) -> dict:
    require_admin(authorization)
    email_address = approve_request(request_id)
    return {"ok": True, "address": email_address, "status": "approved"}


@app.post("/api/v1/admin/requests/{request_id}/reject")
def admin_reject_request(
    request_id: int,
    authorization: str | None = Header(default=None),
) -> dict:
    require_admin(authorization)
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            "UPDATE requests SET status='rejected', encrypted_password=NULL, reviewed_at=? "
            "WHERE id=? AND status='pending'",
            (int(time.time()), request_id),
        ).rowcount
        conn.commit()
        if changed != 1:
            raise HTTPException(status_code=409, detail="This request is no longer pending.")
        return {"ok": True, "status": "rejected"}
    finally:
        conn.close()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Cache-Control", "no-store")
    return response
