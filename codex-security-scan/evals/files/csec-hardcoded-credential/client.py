"""SYNTHETIC EVAL FIXTURE — planted vulnerabilities. Do not deploy."""
import urllib.request

import settings


def fetch_page(page):
    req = urllib.request.Request(
        f"{settings.PARTNER_API_BASE}/rows?page={page}",
        headers={"Authorization": f"Bearer {settings.PARTNER_API_TOKEN}"},
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def dsn():
    return (
        f"host={settings.DB_HOST} user={settings.DB_USER} "
        f"password={settings.DB_PASSWORD} dbname=ingest"
    )
