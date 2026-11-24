"""Explicit opt-in settings for a dedicated demo database alias."""
import os
import re

from django.core.exceptions import ImproperlyConfigured

from .settings import *  # noqa: F401,F403


_demo_required = ("DEMO_DB_NAME", "DEMO_DB_USER", "DEMO_DB_PASSWORD", "DEMO_DB_HOST", "DEMO_DB_PORT")
_demo_missing = [name for name in _demo_required if not os.environ.get(name)]
if _demo_missing:
    raise ImproperlyConfigured("Dedicated demo database settings are incomplete: " + ", ".join(_demo_missing))

_demo_name = os.environ["DEMO_DB_NAME"].strip()
_demo_environment = " ".join((os.environ.get("DJANGO_ENV", ""), os.environ.get("ENVIRONMENT", ""))).casefold().strip()
if re.search(r"(^|[_-])(prod|production)([_-]|$)", _demo_name, re.I):
    raise ImproperlyConfigured("The dedicated demo settings module refuses production-marked database names.")
if _demo_name.casefold() == "eyeviora_clinic" or not re.search(r"(^|[_-])(demo|test)([_-]|$)", _demo_name, re.I):
    raise ImproperlyConfigured("DEMO_DB_NAME must clearly identify an isolated database with a 'demo' or 'test' token.")
if "prod" in _demo_environment or "production" in _demo_environment:
    raise ImproperlyConfigured("The dedicated demo settings module is disabled in production environments.")
if "test" not in _demo_name.casefold() and _demo_environment != "demo":
    raise ImproperlyConfigured("Set DJANGO_ENV=demo before using a non-test demo database.")

_demo_database = {
    "ENGINE": "django.contrib.gis.db.backends.postgis",
    "NAME": _demo_name,
    "USER": os.environ["DEMO_DB_USER"],
    "PASSWORD": os.environ["DEMO_DB_PASSWORD"],
    "HOST": os.environ["DEMO_DB_HOST"],
    "PORT": os.environ["DEMO_DB_PORT"],
}
# This settings module is explicit opt-in. Both app reads and seed writes must
# target the same isolated database; base settings and the default .env stay intact.
DATABASES = {**DATABASES, "default": _demo_database, "demo": _demo_database}
