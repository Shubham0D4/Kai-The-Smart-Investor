"""
Gateway settings, read from environment variables (see .env.example).

Kai runs as a single-user local app: there is no authentication, so by default the server only
listens on 127.0.0.1 and only accepts browser requests from the Vite dev origin. Add
authentication before exposing it on any network (FIXES.md "Add auth before any public
deployment").
"""

import os
from dataclasses import dataclass
from typing import List

# The one user key used wherever a user key is still needed (chat rows, the AP2 mandate).
LOCAL_USER = "local"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"   # the Vite dev server
DEFAULT_DB_URL = "sqlite:///./kai.db"
DEFAULT_MAX_CONCURRENT_DEBATES = 2


@dataclass(frozen=True)
class GatewaySettings:
    host: str
    port: int
    cors_origins: List[str]
    db_url: str
    max_concurrent_debates: int


def load_settings() -> GatewaySettings:
    """Read settings fresh from the environment (call after load_dotenv)."""
    origins = os.getenv("GATEWAY_CORS_ORIGINS") or DEFAULT_CORS_ORIGINS
    return GatewaySettings(
        host=os.getenv("GATEWAY_HOST") or DEFAULT_HOST,
        port=int(os.getenv("GATEWAY_PORT") or DEFAULT_PORT),
        cors_origins=[o.strip() for o in origins.split(",") if o.strip()],
        db_url=os.getenv("KAI_DB_URL") or DEFAULT_DB_URL,
        max_concurrent_debates=int(os.getenv("KAI_MAX_CONCURRENT_DEBATES") or DEFAULT_MAX_CONCURRENT_DEBATES),
    )
