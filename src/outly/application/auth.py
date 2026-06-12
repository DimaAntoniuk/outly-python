from dataclasses import dataclass
from datetime import timedelta, timezone

from ..domain.entities import User
from .errors import BadRequest, Unauthorized
from .ports import GoogleVerifier, RefreshTokenRepository, TokenSigner, UserRepository
from .throttling import utc_now

REFRESH_TOKEN_TTL = timedelta(days=30)


@dataclass(frozen=True)
class AuthResult:
    access_token: str
    refresh_token: str
    user: User


class AuthService:
    def __init__(
        self,
        user_repo: UserRepository,
        refresh_token_repo: RefreshTokenRepository,
        signer: TokenSigner,
        google_verifier: GoogleVerifier,
    ):
        self._user_repo = user_repo
        self._refresh_token_repo = refresh_token_repo
        self._signer = signer
        self._google_verifier = google_verifier

    async def _issue_tokens(self, user: User) -> AuthResult:
        access_token = self._signer.sign_access_token({"id": user.id, "email": user.email})
        refresh_token = self._signer.sign_refresh_token({"id": user.id})
        await self._refresh_token_repo.create(
            refresh_token, user.id, utc_now() + REFRESH_TOKEN_TTL
        )
        return AuthResult(access_token, refresh_token, user)

    async def google_login(self, id_token: str | None) -> AuthResult:
        if not id_token:
            raise BadRequest("idToken is required")
        payload = await self._google_verifier.verify(id_token)
        if payload is None:
            raise Unauthorized("Invalid Google token")
        google_id = payload.get("sub")
        email = payload.get("email")
        name = payload.get("name")
        if not google_id or not email or not name:
            raise BadRequest("Incomplete Google profile")

        user, _ = await self._user_repo.upsert_google_user(
            google_id, email, name, payload.get("picture")
        )
        return await self._issue_tokens(user)

    async def dev_login(self, email: str | None, name: str | None) -> AuthResult:
        if not email or not email.strip():
            raise BadRequest("email is required")
        email = email.strip().lower()
        user, _ = await self._user_repo.upsert_google_user(
            f"dev-{email}", email, name or email.split("@")[0], None
        )
        return await self._issue_tokens(user)

    async def refresh(self, refresh_token: str | None) -> tuple[str, str]:
        if not refresh_token:
            raise Unauthorized("Missing refresh token")
        try:
            payload = self._signer.verify_refresh_token(refresh_token)
        except Exception as error:
            raise Unauthorized("Invalid refresh token") from error

        stored = await self._refresh_token_repo.get_by_token(refresh_token)
        if stored is None or stored.revoked:
            raise Unauthorized("Token revoked")
        expires_at = stored.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < utc_now():
            raise Unauthorized("Refresh token expired")

        await self._refresh_token_repo.revoke(refresh_token)
        user_id = payload["id"]
        new_access = self._signer.sign_access_token({"id": user_id})
        new_refresh = self._signer.sign_refresh_token({"id": user_id})
        await self._refresh_token_repo.create(new_refresh, user_id, utc_now() + REFRESH_TOKEN_TTL)
        return new_access, new_refresh

    async def logout(self, refresh_token: str | None) -> None:
        if refresh_token:
            await self._refresh_token_repo.revoke(refresh_token)
