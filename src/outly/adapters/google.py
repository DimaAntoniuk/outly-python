import asyncio
from typing import Any

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token


class GoogleIdTokenVerifier:
    def __init__(self, client_id: str):
        self._client_id = client_id

    async def verify(self, id_token: str) -> dict[str, Any] | None:
        def _verify() -> dict[str, Any] | None:
            try:
                payload = google_id_token.verify_oauth2_token(
                    id_token, google_requests.Request(), self._client_id
                )
                return dict(payload)
            except Exception:
                return None

        return await asyncio.to_thread(_verify)
