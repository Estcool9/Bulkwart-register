# Existing portal integration

Use this guide when a FastAPI signup portal and its SQLite database already exist. Keep the existing public form, database, encryption key, Stalwart credentials, approval routine and notification flow. Add this admin API to the same application so the Bulwark plugin can manage the same request queue.

## Safety

- Back up the application source and systemd unit before editing.
- Do not replace or re-create the SQLite database.
- Do not copy applicant passwords into API responses or logs.
- Keep the bearer token outside the web root and readable only by the service account.
- Put the public route behind HTTPS.

## Required existing application symbols

The example below assumes the portal already has:
- a FastAPI instance named app;
- a db() helper returning a SQLite connection;
- approval_mode() reading the existing approval-mode setting;
- approve_request(request_id) using the existing Stalwart JMAP code;
- tables named requests and app_settings, with the columns shown in the query/update below.

Adapt the table names only when your portal uses a different schema. Do not create a second queue.

## Adapter routes

Add the imports if they are not already present:

    import hmac
    from pathlib import Path
    from fastapi import HTTPException, Request

Configure ADMIN_API_TOKEN_FILE in the existing systemd unit. The service must read that file on each request, rather than baking a token into source code:

```python
def require_admin_api(request: Request) -> None:
    token_path = os.environ.get("ADMIN_API_TOKEN_FILE", "")
    try:
        expected = Path(token_path).read_text(encoding="utf-8").strip() if token_path else ""
    except OSError:
        expected = ""
    if not expected:
        raise HTTPException(status_code=503, detail="The admin API token is not configured.")
    supplied = request.headers.get("authorization", "")
    if not hmac.compare_digest(supplied, "Bearer " + expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/api/v1/admin/settings")
def admin_get_settings(request: Request):
    require_admin_api(request)
    return {
        "approvalMode": approval_mode(),
        "notificationsEnabled": bool(SMTP_HOST and ADMIN),
        "notificationEmail": ADMIN if SMTP_HOST and ADMIN else None,
    }


@app.put("/api/v1/admin/settings")
async def admin_update_settings(request: Request):
    require_admin_api(request)
    payload = await request.json()
    mode = payload.get("approvalMode") if isinstance(payload, dict) else None
    if mode not in {"manual", "auto"}:
        raise HTTPException(status_code=422, detail="Invalid approvalMode")
    conn = db()
    try:
        conn.execute(
            "INSERT INTO app_settings(key,value) VALUES('approval_mode',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (mode,),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "approvalMode": mode}


@app.get("/api/v1/admin/requests")
def admin_list_requests(request: Request):
    require_admin_api(request)
    conn = db()
    try:
        rows = conn.execute(
            "SELECT id,localpart,domain,created_at FROM requests "
            "WHERE status='pending' ORDER BY created_at ASC"
        ).fetchall()
        return {"requests": [
            {"id": row["id"], "address": row["localpart"] + "@" + row["domain"],
             "createdAt": row["created_at"]}
            for row in rows
        ]}
    finally:
        conn.close()


@app.post("/api/v1/admin/requests/{request_id}/approve")
def admin_approve_request(request_id: int, request: Request):
    require_admin_api(request)
    try:
        address = approve_request(request_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)[:240]) from exc
    return {"ok": True, "address": address, "status": "approved"}


@app.post("/api/v1/admin/requests/{request_id}/reject")
def admin_reject_request(request_id: int, request: Request):
    require_admin_api(request)
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            "UPDATE requests SET status='rejected',encrypted_password=NULL,reviewed_at=? "
            "WHERE id=? AND status='pending'",
            (int(time.time()), request_id),
        ).rowcount
        if changed != 1:
            conn.rollback()
            raise HTTPException(status_code=409, detail="Request is no longer pending.")
        conn.commit()
        return {"ok": True, "status": "rejected"}
    finally:
        conn.close()
```

The import list above includes os and time because most signup portals already use them; add either import if absent.

## Systemd secret configuration

Add one environment line to the existing unit:

```ini
Environment=ADMIN_API_TOKEN_FILE=/path/to/admin_api_token
```

Create the token file outside the web root, owned by the signup service account, mode 0400. Never commit it.

## Reverse proxy and Bulwark configuration

The public proxy must strip the existing signup prefix when forwarding to FastAPI, the same way it already forwards the signup form.

For example:
- Public origin: https://webmail.example.net
- Public path: /signup/
- Backend: existing FastAPI listener, path prefix stripped
- Plugin Signup service URL: https://webmail.example.net
- Plugin Signup service path: /signup

If the service API is at the origin root, use / as the plugin service path. Keep HTTPS and restrict direct access to the backend listener.

## Validation

1. Existing signup page remains HTTP 200.
2. Existing health endpoint remains HTTP 200.
3. Admin API without a token returns 401 (or 503 before token setup).
4. Authenticated settings and request-list calls return 200.
5. Returned requests correspond to the existing SQLite queue; no applicant password is exposed.
6. Test approve/reject with a disposable request before using the controls on real pending requests.
