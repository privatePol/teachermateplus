import importlib.util
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent.parent
DJANGO_ENV = os.getenv("DJANGO_ENV", "local").lower()


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "change-this-in-production")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]
TRUSTED_PROXY_IPS = [
    value.strip()
    for value in os.getenv("DJANGO_TRUSTED_PROXY_IPS", "").split(",")
    if value.strip()
]
TRUST_UNIX_SOCKET_PROXY = env_bool("DJANGO_TRUST_UNIX_SOCKET_PROXY", False)
DJANGO_ADMIN_PATH = (
    os.getenv("DJANGO_ADMIN_PATH", "django-admin").strip("/")
    or "django-admin"
)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.core.apps.CoreConfig",
    "apps.accounts.apps.AccountsConfig",
    "apps.rbac.apps.RbacConfig",
    "apps.auditlog.apps.AuditlogConfig",
    "apps.navigation.apps.NavigationConfig",
    "apps.tenants.apps.TenantsConfig",
    "apps.academics.apps.AcademicsConfig",
    "apps.students.apps.StudentsConfig",
    "apps.faculty.apps.FacultyConfig",
    "apps.enrollment.apps.EnrollmentConfig",
    "apps.imports.apps.ImportsConfig",
    "apps.grading.apps.GradingConfig",
    "apps.attendance.apps.AttendanceConfig",
    "apps.notifications.apps.NotificationsConfig",
    "apps.predictions.apps.PredictionsConfig",
    "apps.student_portal.apps.StudentPortalConfig",
    "apps.admin_portal.apps.AdminPortalConfig",
    "apps.faculty_portal.apps.FacultyPortalConfig",
]

DEBUG_TOOLBAR_ENABLED = (
    DEBUG
    and env_bool("DJANGO_DEBUG_TOOLBAR", False)
    and importlib.util.find_spec("debug_toolbar") is not None
)
if DEBUG_TOOLBAR_ENABLED:
    INSTALLED_APPS.append("debug_toolbar")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.ScopeResolutionMiddleware",
    "apps.core.middleware.SessionTimeoutMiddleware",
    "apps.core.middleware.PortalCacheControlMiddleware",
    "apps.core.middleware.PortalAccessMiddleware",
    "apps.core.middleware.PostLoginSecurityMiddleware",
]
if DEBUG_TOOLBAR_ENABLED:
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")
    INTERNAL_IPS = [ip.strip() for ip in os.getenv("DJANGO_INTERNAL_IPS", "127.0.0.1").split(",") if ip.strip()]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.portal_menu",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DB_ENGINE = os.getenv("DB_ENGINE", "django.db.backends.sqlite3")
if DB_ENGINE == "django.db.backends.sqlite3":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("DB_NAME", str(BASE_DIR / "db.sqlite3")),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": DB_ENGINE,
            "NAME": os.getenv("DB_NAME", "teachermateplus"),
            "USER": os.getenv("DB_USER", "root"),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "127.0.0.1"),
            "PORT": os.getenv("DB_PORT", "3306"),
            "OPTIONS": {"charset": "utf8mb4"},
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "apps.accounts.validators.StrongPasswordComplexityValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("DJANGO_TIME_ZONE", "Asia/Manila")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

CACHES = {
    "default": {
        "BACKEND": os.getenv("DJANGO_CACHE_BACKEND", "django.core.cache.backends.locmem.LocMemCache"),
        "LOCATION": os.getenv("DJANGO_CACHE_LOCATION", "teachermateplus-local"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "accounts:admin_login"
LOGIN_REDIRECT_URL = "admin_portal:dashboard"
LOGOUT_REDIRECT_URL = "accounts:admin_login"

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "no-reply@teachermateplus.local")
EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "25"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
EMAIL_USE_SSL = env_bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "10"))
EMAIL_LOGO_URL = os.getenv("EMAIL_LOGO_URL", "")
EMAIL_LOGO_PATH = os.getenv("EMAIL_LOGO_PATH", "")
EMAIL_SCHOOL_LOGO_URL = os.getenv("EMAIL_SCHOOL_LOGO_URL", "")
EMAIL_SCHOOL_LOGO_PATH = os.getenv("EMAIL_SCHOOL_LOGO_PATH", "")
SIS_API_TOKEN = os.getenv("SIS_API_TOKEN", "")
SIS_API_LEGACY_TOKEN_ENABLED = env_bool("SIS_API_LEGACY_TOKEN_ENABLED", True)
SIS_API_RATE_LIMIT_PER_MINUTE = int(os.getenv("SIS_API_RATE_LIMIT_PER_MINUTE", "60"))
PRIVACY_CONSENT_VERSION = os.getenv("PRIVACY_CONSENT_VERSION", "2026-03")
ENFORCE_SINGLE_DEVICE_SESSION = env_bool("ENFORCE_SINGLE_DEVICE_SESSION", True)
MAINTENANCE_MODE = env_bool("MAINTENANCE_MODE", False)
ACTUAL_DATA_RESET_ALLOW_PRODUCTION = env_bool("ACTUAL_DATA_RESET_ALLOW_PRODUCTION", False)
ACTUAL_DATA_RESET_EXTERNAL_BACKUP_CONFIRMED = env_bool("ACTUAL_DATA_RESET_EXTERNAL_BACKUP_CONFIRMED", False)

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = int(os.getenv("DJANGO_SESSION_TIMEOUT_SECONDS", "3600"))
SESSION_SAVE_EVERY_REQUEST = env_bool("DJANGO_SESSION_SAVE_EVERY_REQUEST", True)
SESSION_EXPIRE_AT_BROWSER_CLOSE = env_bool("DJANGO_SESSION_EXPIRE_AT_BROWSER_CLOSE", False)
X_FRAME_OPTIONS = "DENY"
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

LOG_DIR = Path(os.getenv("DJANGO_LOG_DIR", str(BASE_DIR / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
        "system_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "system.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "standard",
        },
        "error_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "errors.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 10,
            "formatter": "standard",
            "level": "ERROR",
        },
        "security_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(LOG_DIR / "security.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 10,
            "formatter": "standard",
        },
    },
    "loggers": {
        "django": {"handlers": ["console", "system_file"], "level": "INFO", "propagate": True},
        "django.request": {"handlers": ["console", "error_file"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["console", "security_file"], "level": "WARNING", "propagate": False},
        "teachermateplus.api": {"handlers": ["console", "security_file"], "level": "INFO", "propagate": False},
        "teachermateplus.system": {"handlers": ["console", "system_file"], "level": "INFO", "propagate": False},
    },
}
