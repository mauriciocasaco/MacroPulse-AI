"""Load the existing DEV settings inside the backend process tree only."""

import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    try:
        import langgraph.graph  # noqa: F401
        import langchain_google_genai  # noqa: F401
        import uvicorn  # noqa: F401
        from backend.market_data.api_config import MarketApiConfigError, _read_dotenv, _split_dev_config
        from backend.market_data.dev_import import DEV_PROJECT_REF
    except ImportError:
        return 10

    try:
        shared = _read_dotenv('.env')
        for key in ('GEMINI_API_KEY', 'MARKETPULSE_GEMINI_MODEL'):
            if shared.get(key):
                os.environ.setdefault(key, shared[key])
        os.environ.setdefault('MARKETPULSE_DEV_PROJECT_REF', shared.get('MARKETPULSE_DEV_PROJECT_REF') or DEV_PROJECT_REF)
        _split_dev_config(shared)
    except MarketApiConfigError:
        return 11
    if not os.environ.get('GEMINI_API_KEY', '').strip():
        return 12

    # The local API validates this exact Uvicorn launch shape.
    return subprocess.call([
        sys.executable, '-m', 'uvicorn', 'backend.api:app', '--host', '127.0.0.1', '--port', '8000',
    ])


if __name__ == '__main__':
    sys.exit(main())
