from django.core.exceptions import ImproperlyConfigured

from .settings import *
from .settings import env

SECRET_KEY = env("SECRET_KEY")
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5:
    raise ImproperlyConfigured(
        "SECRET_KEY debe ser estable y criptográficamente segura."
    )

DEBUG = False
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")
if not ALLOWED_HOSTS or any(
    not host or "*" in host or "/" in host or ":" in host for host in ALLOWED_HOSTS
):
    raise ImproperlyConfigured("ALLOWED_HOSTS debe contener dominios explícitos.")
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
# Only the loopback Gunicorn listener may receive Caddy's overwritten header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS")
if SECURE_HSTS_SECONDS < 0:
    raise ImproperlyConfigured("SECURE_HSTS_SECONDS no puede ser negativo.")
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

DATABASES = {"default": env.db()}
database = DATABASES["default"]
if (
    database["ENGINE"] != "django.db.backends.postgresql"
    or database["NAME"] != "sdiego_backoffice"
    or database["USER"] not in {"sdiego_app", "sdiego_migrator"}
    or database["HOST"] != "127.0.0.1"
):
    raise ImproperlyConfigured(
        "Utilice la base y el rol exclusivos en PostgreSQL local."
    )

if database["USER"] == "sdiego_migrator":
    database.setdefault("OPTIONS", {})["options"] = "-c role=sdiego_owner"

STATIC_URL = "/static/"
STATIC_ROOT = env.path("STATIC_ROOT")
MEDIA_ROOT = env.path("MEDIA_ROOT")

email_backend = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend"
)
MAILERS = {"default": {"BACKEND": email_backend}}
if email_backend == "django.core.mail.backends.smtp.EmailBackend":
    MAILERS["default"]["OPTIONS"] = {
        "host": env("EMAIL_HOST"),
        "port": env.int("EMAIL_PORT"),
        "username": env("EMAIL_HOST_USER"),
        "password": env("EMAIL_HOST_PASSWORD"),
        "use_tls": True,
        "timeout": 10,
    }
    DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL")
    if not all(
        MAILERS["default"]["OPTIONS"][field]
        for field in ("host", "username", "password")
    ) or not DEFAULT_FROM_EMAIL:
        raise ImproperlyConfigured(
            "SMTP requiere servidor, credenciales y remitente reales."
        )
    SERVER_EMAIL = DEFAULT_FROM_EMAIL
elif email_backend != "django.core.mail.backends.dummy.EmailBackend":
    raise ImproperlyConfigured("Seleccione el backend SMTP o dummy de Django.")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "safe": {"()": "sdiego_backoffice.production_logging.SafeLogFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "safe",
        },
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.server": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
