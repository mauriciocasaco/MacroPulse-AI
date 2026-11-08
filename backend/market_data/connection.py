"""Secure PostgreSQL connections for the Market Data schema."""

from __future__ import annotations

from pathlib import Path

from psycopg.conninfo import make_conninfo


_CA_CERTIFICATE = Path(__file__).resolve().parents[2] / "Certificate" / "prod-ca-2021.crt"


def secure_supabase_dsn(dsn: str) -> str:
    """Add strict Supabase TLS settings while preserving the supplied DSN."""
    return make_conninfo(
        dsn,
        sslmode="verify-full",
        sslrootcert=str(_CA_CERTIFICATE),
        gssencmode="disable",
    )
