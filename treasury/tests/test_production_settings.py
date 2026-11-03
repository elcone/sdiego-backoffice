import logging
import os
import secrets
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

from sdiego_backoffice.production_logging import SafeLogFormatter


class ProductionSettingsTests(SimpleTestCase):
    def environment(self):
        return {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "sdiego_backoffice.settings_production",
            "SECRET_KEY": secrets.token_urlsafe(64),
            "DATABASE_URL": "postgresql://sdiego_app@127.0.0.1/sdiego_backoffice",
            "ALLOWED_HOSTS": "sdiego.example.invalid",
            "SECURE_HSTS_SECONDS": "300",
            "STATIC_ROOT": "/tmp/sdiego-test-static",
            "MEDIA_ROOT": "/tmp/sdiego-test-private",
            "EMAIL_HOST": "smtp.example.invalid",
            "EMAIL_PORT": "587",
            "EMAIL_HOST_USER": "test-user",
            "EMAIL_HOST_PASSWORD": secrets.token_urlsafe(32),
            "DEFAULT_FROM_EMAIL": "test@example.invalid",
        }

    def run_settings(self, environment, code):
        return subprocess.run(
            [sys.executable, "-c", code],
            env=environment,
            cwd=Path(__file__).resolve().parents[2],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_production_security_and_shared_proxy_contract(self):
        result = self.run_settings(
            self.environment(),
            """
import django
django.setup()
from django.conf import settings as s
from django.test import Client, RequestFactory
assert not s.DEBUG
assert s.ALLOWED_HOSTS == ['sdiego.example.invalid']
assert s.CSRF_TRUSTED_ORIGINS == ['https://sdiego.example.invalid']
assert s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE
assert s.SESSION_COOKIE_HTTPONLY
assert s.SECURE_SSL_REDIRECT
assert s.SECURE_PROXY_SSL_HEADER == ('HTTP_X_FORWARDED_PROTO', 'https')
assert RequestFactory().get('/', HTTP_X_FORWARDED_PROTO='https').is_secure()
assert not RequestFactory().get('/').is_secure()
assert s.DATABASES['default']['USER'] == 'sdiego_app'
assert s.DATABASES['default']['HOST'] == '127.0.0.1'
assert s.STATIC_ROOT != s.MEDIA_ROOT
assert not s.SECURE_HSTS_INCLUDE_SUBDOMAINS and not s.SECURE_HSTS_PRELOAD
client = Client(enforce_csrf_checks=True)
response = client.get('/api/auth/session/', secure=True, HTTP_HOST='sdiego.example.invalid')
assert response.status_code == 200
assert response.json()['authenticated'] is False
assert response.cookies['csrftoken']['secure']
assert client.post('/api/auth/login/', {}, secure=True, HTTP_HOST='sdiego.example.invalid').status_code == 403
assert client.get('/api/auth/logout/', secure=True, HTTP_HOST='sdiego.example.invalid').status_code == 405
assert client.get('/api/auth/session/', HTTP_HOST='sdiego.example.invalid').status_code == 301
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_required_environment_and_exclusive_database(self):
        for key in ("SECRET_KEY", "ALLOWED_HOSTS", "DATABASE_URL"):
            with self.subTest(key=key):
                environment = self.environment()
                environment.pop(key)
                result = self.run_settings(environment, "import django; django.setup()")
                self.assertNotEqual(result.returncode, 0)
        for key, value in [
            ("SECRET_KEY", ""),
            ("ALLOWED_HOSTS", "*"),
            ("DATABASE_URL", "postgresql://postgres@127.0.0.1/other_database"),
        ]:
            with self.subTest(key=key):
                environment = self.environment()
                environment[key] = value
                result = self.run_settings(environment, "import django; django.setup()")
                self.assertNotEqual(result.returncode, 0)

    def test_migrator_explicitly_assumes_owner_role(self):
        environment = self.environment()
        environment["DATABASE_URL"] = (
            "postgresql://sdiego_migrator@127.0.0.1/sdiego_backoffice"
        )
        result = self.run_settings(
            environment,
            """
import django
django.setup()
from django.conf import settings
assert settings.DATABASES['default']['OPTIONS']['options'] == '-c role=sdiego_owner'
""",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_deployment_check_has_no_errors_and_no_checks_silenced(self):
        result = subprocess.run(
            [sys.executable, "manage.py", "check", "--deploy"],
            env=self.environment(),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0 silenced", result.stderr)

    def test_logging_omits_credentials_and_exception_details(self):
        record = logging.LogRecord(
            "django.request",
            logging.ERROR,
            __file__,
            1,
            "password=secret /reset/sensitive-token/?csrf=secret",
            (),
            (RuntimeError, RuntimeError("secret exception"), None),
        )
        record.status_code = 500
        self.assertEqual(
            SafeLogFormatter().format(record), "ERROR django.request status=500"
        )
