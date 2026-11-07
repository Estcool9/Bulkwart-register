import os
import tempfile
import unittest

from cryptography.fernet import Fernet

TEMP_DIR = tempfile.TemporaryDirectory()
os.environ.setdefault("APP_NAME", "Test Signup")
os.environ.setdefault("PUBLIC_BASE_URL", "https://signup.example.test")
os.environ.setdefault("DB_PATH", os.path.join(TEMP_DIR.name, "test.sqlite3"))
os.environ.setdefault("STALWART_URL", "https://mail.example.test")
os.environ.setdefault("STALWART_ADMIN_USER", "admin@example.test")
os.environ.setdefault("STALWART_ADMIN_PASSWORD", "not-a-real-password")
os.environ.setdefault("STALWART_DOMAINS_JSON", '{"example.test":"test-domain-id"}')
os.environ.setdefault("ADMIN_API_TOKEN", "test-token-" + ("x" * 48))
os.environ.setdefault("FIELD_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))

from fastapi.testclient import TestClient
from service.app import app, db

client = TestClient(app)


class CompanionApiTests(unittest.TestCase):
    def setUp(self):
        conn = db()
        conn.execute("DELETE FROM requests")
        conn.execute("DELETE FROM rate_events")
        conn.execute("UPDATE settings SET value='manual' WHERE key='approval_mode'")
        conn.commit()
        conn.close()

    def admin_headers(self):
        return {"Authorization": "Bearer " + os.environ["ADMIN_API_TOKEN"]}

    def test_health_endpoint(self):
        response = client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "ok")

    def test_admin_api_requires_bearer_token(self):
        response = client.get("/api/v1/admin/requests")
        self.assertEqual(response.status_code, 401)

    def test_admin_api_lists_pending_requests_without_secrets(self):
        conn = db()
        conn.execute(
            "INSERT INTO requests(localpart, domain, domain_id, encrypted_password, status, client_ip, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("alice", "example.test", "test-domain-id", "encrypted-value", "pending", "127.0.0.1", 1000),
        )
        conn.commit()
        conn.close()

        response = client.get("/api/v1/admin/requests", headers=self.admin_headers())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["requests"][0]["address"], "alice@example.test")
        self.assertNotIn("encrypted_password", response.text)
        self.assertNotIn("encrypted-value", response.text)

    def test_mode_can_be_changed(self):
        response = client.put(
            "/api/v1/admin/settings",
            json={"approvalMode": "auto"},
            headers=self.admin_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["approvalMode"], "auto")

        settings = client.get("/api/v1/admin/settings", headers=self.admin_headers())
        self.assertEqual(settings.json()["approvalMode"], "auto")

    def test_invalid_mode_is_rejected(self):
        response = client.put(
            "/api/v1/admin/settings",
            json={"approvalMode": "sometimes"},
            headers=self.admin_headers(),
        )
        self.assertEqual(response.status_code, 422)

    def test_reject_erases_encrypted_password(self):
        conn = db()
        conn.execute(
            "INSERT INTO requests(localpart, domain, domain_id, encrypted_password, status, client_ip, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            ("bob", "example.test", "test-domain-id", "encrypted-value", "pending", "127.0.0.1", 1000),
        )
        request_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        response = client.post(
            "/api/v1/admin/requests/" + str(request_id) + "/reject",
            headers=self.admin_headers(),
        )
        self.assertEqual(response.status_code, 200)

        conn = db()
        row = conn.execute("SELECT status, encrypted_password FROM requests WHERE id=?", (request_id,)).fetchone()
        conn.close()
        self.assertEqual(row["status"], "rejected")
        self.assertIsNone(row["encrypted_password"])


if __name__ == "__main__":
    unittest.main()
