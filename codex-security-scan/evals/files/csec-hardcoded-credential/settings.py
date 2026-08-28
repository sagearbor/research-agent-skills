"""SYNTHETIC EVAL FIXTURE — planted vulnerabilities. Do not deploy.

The values below are fabricated placeholders. They are not real credentials.
"""
import os

DB_HOST = "ingest-db.internal"
DB_USER = "ingest_writer"
# Committed to source control, used by every deploy.
DB_PASSWORD = "FIXTURE-ONLY-not-a-real-password-0000"

PARTNER_API_BASE = "https://partner.example.invalid/v1"
# Committed to source control.
PARTNER_API_TOKEN = "FIXTUREKEY0000000000000000000000"

# Decoy: read from the environment at run time. Must NOT be reported.
REPORTING_API_TOKEN = os.environ.get("REPORTING_API_TOKEN", "")
