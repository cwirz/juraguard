import os
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import validate_email


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / ".data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() == "true"


DEPLOYMENT_MODE = os.environ.get("DEPLOYMENT_MODE", "self_hosted")
if DEPLOYMENT_MODE not in {"self_hosted", "cloud"}:
    raise ImproperlyConfigured("DEPLOYMENT_MODE must be 'self_hosted' or 'cloud'.")
CLOUD_MODE = DEPLOYMENT_MODE == "cloud"


def persistent_secret() -> str:
    if value := os.environ.get("DJANGO_SECRET_KEY"):
        return value
    if CLOUD_MODE:
        raise ImproperlyConfigured("Cloud mode requires an explicit non-empty DJANGO_SECRET_KEY.")
    path = DATA_DIR / "secret_key"
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        value = secrets.token_urlsafe(64)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as secret_file:
            secret_file.write(value)
        return value


SECRET_KEY = persistent_secret()


def persistent_owner_setup_deadline() -> int:
    path = DATA_DIR / "owner_setup_deadline"
    try:
        return int(path.read_text().strip())
    except FileNotFoundError:
        deadline = int(time.time()) + 15 * 60
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return int(path.read_text().strip())
        with os.fdopen(descriptor, "w") as deadline_file:
            deadline_file.write(str(deadline))
        return deadline


def persistent_owner_setup_token() -> str:
    path = DATA_DIR / "owner_setup_token"
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        value = secrets.token_urlsafe(32)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return path.read_text().strip()
        with os.fdopen(descriptor, "w") as token_file:
            token_file.write(value)
        return value


OWNER_SETUP_DEADLINE = persistent_owner_setup_deadline() if not CLOUD_MODE else 0
OWNER_SETUP_TOKEN = persistent_owner_setup_token() if not CLOUD_MODE else ""
DEBUG = env_bool("DEBUG")
ALLOWED_HOSTS = [host.strip() for host in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")]
CSRF_TRUSTED_ORIGINS = [origin for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if origin]
MCP_ALLOWED_ORIGINS = {origin for origin in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",") if origin}
ALLOW_PRIVATE_NETWORKS = env_bool("ALLOW_PRIVATE_NETWORKS")
CREDENTIAL_ENCRYPTION_KEYS = [
    value.strip() for value in os.environ.get("CREDENTIAL_ENCRYPTION_KEYS", "").split(",") if value.strip()
]
PRODUCT_NAME = os.environ.get("PRODUCT_NAME", "Juraguard")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")

POLAR_BILLING_ENABLED = CLOUD_MODE and env_bool("POLAR_BILLING_ENABLED", True)
POLAR_ACCESS_TOKEN = os.environ.get("POLAR_ACCESS_TOKEN", "")
POLAR_WEBHOOK_SECRET = os.environ.get("POLAR_WEBHOOK_SECRET", "")
POLAR_MONTHLY_PRODUCT_ID = os.environ.get("POLAR_MONTHLY_PRODUCT_ID", "")
POLAR_ANNUAL_PRODUCT_ID = os.environ.get("POLAR_ANNUAL_PRODUCT_ID", "")
POLAR_BETA_DISCOUNT_ID = os.environ.get("POLAR_BETA_DISCOUNT_ID", "")
POLAR_SERVER_URL = os.environ.get("POLAR_SERVER_URL", "https://api.polar.sh")
CLOUD_BETA_ACCESS = env_bool("CLOUD_BETA_ACCESS")
POLAR_ALLOW_CUSTOM_SERVER_URL = env_bool("POLAR_ALLOW_CUSTOM_SERVER_URL")
if POLAR_BILLING_ENABLED:
    required = {
        "POLAR_ACCESS_TOKEN": POLAR_ACCESS_TOKEN,
        "POLAR_WEBHOOK_SECRET": POLAR_WEBHOOK_SECRET,
        "POLAR_MONTHLY_PRODUCT_ID": POLAR_MONTHLY_PRODUCT_ID,
        "POLAR_ANNUAL_PRODUCT_ID": POLAR_ANNUAL_PRODUCT_ID,
        "POLAR_BETA_DISCOUNT_ID": POLAR_BETA_DISCOUNT_ID,
        "PUBLIC_BASE_URL": PUBLIC_BASE_URL,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ImproperlyConfigured(f"Cloud billing requires: {', '.join(missing)}.")
    polar_url = urlsplit(POLAR_SERVER_URL)
    polar_origin = f"{polar_url.scheme}://{polar_url.netloc}"
    official_origins = {"https://api.polar.sh", "https://sandbox-api.polar.sh"}
    custom_test_origin = DEBUG and POLAR_ALLOW_CUSTOM_SERVER_URL
    if polar_url.scheme != "https":
        raise ImproperlyConfigured("POLAR_SERVER_URL must use HTTPS.")
    if (
        polar_origin not in official_origins
        or polar_url.path.rstrip("/")
        or polar_url.query
        or polar_url.fragment
    ) and not custom_test_origin:
        raise ImproperlyConfigured("POLAR_SERVER_URL must be an official Polar HTTPS API origin.")

LICENSE_VALIDATION_URL = os.environ.get("LICENSE_VALIDATION_URL", "")
LICENSE_VALIDATION_TIMEOUT = float(os.environ.get("LICENSE_VALIDATION_TIMEOUT", "5"))
LICENSE_GRACE_DAYS = int(os.environ.get("LICENSE_GRACE_DAYS", "7"))
LICENSE_DOCUMENT_CLOCK_SKEW_SECONDS = int(os.environ.get("LICENSE_DOCUMENT_CLOCK_SKEW_SECONDS", "300"))
LICENSE_DOCUMENT_MAX_LIFETIME_SECONDS = int(os.environ.get("LICENSE_DOCUMENT_MAX_LIFETIME_SECONDS", "86400"))
LICENSE_ISSUER = os.environ.get("LICENSE_ISSUER", "juraguard")
LICENSE_AUDIENCE = os.environ.get("LICENSE_AUDIENCE", "juraguard-self-hosted")
LICENSE_SIGNING_PUBLIC_KEY = os.environ.get("LICENSE_SIGNING_PUBLIC_KEY", "")
LICENSE_SIGNING_PRIVATE_KEY = os.environ.get("LICENSE_SIGNING_PRIVATE_KEY", "")
LICENSE_ENTITLEMENTS = {"organization_controls"}

INSTALLED_APPS = [
    "django.contrib.sites",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.github",
    "allauth.socialaccount.providers.google",
    "gateway.apps.GatewayConfig",
    "commercial.apps.CommercialConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "gateway.hardening.SecurityMiddleware",
    "gateway.middleware.DeploymentModeMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "gateway.context.product",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"


def database_config():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "db.sqlite3"}
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path.strip("/"):
        raise ImproperlyConfigured("DATABASE_URL must be a PostgreSQL URL with a host and database name.")
    config = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname,
        "PORT": parsed.port or 5432,
    }
    if sslmode := parse_qs(parsed.query).get("sslmode", [""])[0]:
        config["OPTIONS"] = {"sslmode": sslmode}
    return config


DATABASES = {"default": database_config()}
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = DATA_DIR / "static"
STORAGES = {"staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SITE_ID = 1
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory" if CLOUD_MODE else "none"
ACCOUNT_CONFIRM_EMAIL_ON_GET = True
ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION = True
ACCOUNT_EMAIL_CONFIRMATION_ANONYMOUS_REDIRECT_URL = "account_login"
ACCOUNT_EMAIL_SUBJECT_PREFIX = "[JuraGuard] "
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_PREVENT_ENUMERATION = True
ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS = False
SOCIALACCOUNT_ONLY = False
SOCIALACCOUNT_PROVIDERS = {
    provider: {"APPS": [{"client_id": os.environ.get(f"{provider.upper()}_CLIENT_ID", ""),
                          "secret": os.environ.get(f"{provider.upper()}_CLIENT_SECRET", ""), "key": ""}]}
    for provider in ("google", "github")
    if os.environ.get(f"{provider.upper()}_CLIENT_ID") and os.environ.get(f"{provider.upper()}_CLIENT_SECRET")
}
CONSOLE_EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@localhost")
if not CLOUD_MODE and not DEBUG and EMAIL_BACKEND == CONSOLE_EMAIL_BACKEND:
    raise ImproperlyConfigured("Console EMAIL_BACKEND is not allowed outside DEBUG mode.")
if CLOUD_MODE:
    if not EMAIL_BACKEND or EMAIL_BACKEND == CONSOLE_EMAIL_BACKEND or "EMAIL_BACKEND" not in os.environ:
        raise ImproperlyConfigured("Cloud mode requires an explicit non-console EMAIL_BACKEND.")
    try:
        validate_email(os.environ.get("DEFAULT_FROM_EMAIL", ""))
        if "." not in DEFAULT_FROM_EMAIL.rsplit("@", 1)[-1]:
            raise ValidationError("Cloud sender domain must be public.")
    except ValidationError as exc:
        raise ImproperlyConfigured("Cloud mode requires an explicit valid DEFAULT_FROM_EMAIL.") from exc
LOGIN_URL = "account_login" if CLOUD_MODE else "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "landing"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", CLOUD_MODE)
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", CLOUD_MODE)
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", CLOUD_MODE)
SECURE_REDIRECT_EXEMPT = [r"^health(?:/|$)"]
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000" if CLOUD_MODE else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS")
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD")
TRUST_PROXY_HEADERS = env_bool("TRUST_PROXY_HEADERS")
if TRUST_PROXY_HEADERS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact_request_secrets": {"()": "gateway.logging_filters.RedactRequestSecrets"}},
    "handlers": {"console": {
        "class": "logging.StreamHandler", "formatter": "plain", "filters": ["redact_request_secrets"],
    }},
    "formatters": {"plain": {"format": "%(levelname)s %(name)s %(message)s"}},
    "loggers": {
        "juraguard.access": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "juraguard.security": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.security.csrf": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
