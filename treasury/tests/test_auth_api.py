from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import resolve


class AuthAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="operator", password="test-password"
        )
        get_user_model().objects.create_user(
            username="inactive", password="test-password", is_active=False
        )
        cls.user.user_permissions.add(
            Permission.objects.get(
                codename="add_charge", content_type__app_label="treasury"
            )
        )

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def bootstrap(self):
        response = self.client.get("/api/auth/session/")
        self.assertEqual(response.status_code, 200)
        return response.json()["csrf_token"]

    def post_login(self, token, username="operator", password="test-password"):
        return self.client.post(
            "/api/auth/login/",
            {"username": username, "password": password},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

    def test_anonymous_session_and_csrf_required(self):
        token = self.bootstrap()
        data = self.client.get("/api/auth/session/").json()
        self.assertFalse(data["authenticated"])
        self.assertIsNone(data["user"])
        self.assertEqual(data["permissions"], [])
        self.assertIn("csrftoken", self.client.cookies)
        self.assertEqual(self.post_login("").status_code, 403)
        self.assertEqual(self.post_login(token).status_code, 200)

    def test_login_rotation_recovery_logout_and_viewflow(self):
        token = self.bootstrap()
        old_csrf = self.client.cookies["csrftoken"].value
        session = self.client.session
        session["test"] = True
        session.save()
        old_key = session.session_key
        response = self.post_login(token)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["user"], {"username": "operator"})
        self.assertEqual(data["permissions"], ["treasury.add_charge"])
        self.assertNotEqual(old_key, self.client.session.session_key)
        self.assertNotEqual(old_csrf, self.client.cookies["csrftoken"].value)
        self.assertEqual(
            self.client.get("/api/auth/session/").json()["user"], data["user"]
        )
        self.assertEqual(self.client.get("/api/house/").status_code, 200)
        self.assertEqual(self.client.get("/accounts/login/").status_code, 200)
        self.assertIsNotNone(resolve("/accounts/logout/"))
        self.assertEqual(self.client.get("/api/auth/logout/").status_code, 405)
        self.assertEqual(self.client.post("/api/auth/logout/").status_code, 403)
        self.assertEqual(
            self.client.post("/api/auth/logout/", HTTP_X_CSRFTOKEN=token).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/api/auth/logout/", HTTP_X_CSRFTOKEN=data["csrf_token"]
            ).status_code,
            200,
        )
        self.assertFalse(self.client.get("/api/auth/session/").json()["authenticated"])
        self.assertEqual(self.client.get("/api/house/").status_code, 403)

    def test_invalid_and_inactive_are_indistinguishable(self):
        token = self.bootstrap()
        responses = [
            self.post_login(token, "missing"),
            self.post_login(token, "operator", "wrong"),
            self.post_login(token, "inactive"),
        ]
        for response in responses:
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {"detail": "Credenciales inválidas."})
        self.assertFalse(self.client.get("/api/auth/session/").json()["authenticated"])

    def test_methods_and_invalid_payload(self):
        token = self.bootstrap()
        self.assertEqual(self.client.get("/api/auth/login/").status_code, 405)
        self.assertEqual(self.client.post("/api/auth/session/").status_code, 403)
        for payload in ["[]", "null", "{", '{"username": [], "password": 1}']:
            self.assertEqual(
                self.client.post(
                    "/api/auth/login/",
                    payload,
                    content_type="application/json",
                    HTTP_X_CSRFTOKEN=token,
                ).status_code,
                400,
            )
