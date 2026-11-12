"""Availability of the experimental loopback research flow."""

import os


def research_v2_enabled() -> bool:
    return (os.environ.get("MARKETPULSE_MODE", "local") in {"local", "test"}
            and os.environ.get("MARKETPULSE_MACRO_AI_ENABLED") == "true"
            and os.environ.get("MARKETPULSE_RESEARCH_V2_ENABLED") == "true")
