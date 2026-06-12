import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HEX_KEY_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
HEX_PATTERN = re.compile(r"^[0-9a-fA-F]+$")
IV_LENGTH = 16
DURATION_PATTERN = re.compile(r"^(\d+)([smhd])$")
DURATION_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def parse_duration(value: str) -> timedelta:
    match = DURATION_PATTERN.match(value.strip())
    if not match:
        raise ValueError(f"Invalid duration: {value}")
    amount, unit = match.groups()
    return timedelta(**{DURATION_UNITS[unit]: int(amount)})


class AesCredentialCipher:
    def __init__(self, hex_key: str):
        if not HEX_KEY_PATTERN.match(hex_key):
            raise ValueError("ENCRYPTION_KEY must be 64 hex characters (32 bytes)")
        self._key = bytes.fromhex(hex_key)

    def encrypt(self, plain_text: str) -> str:
        iv = os.urandom(IV_LENGTH)
        padder = padding.PKCS7(128).padder()
        padded = padder.update(plain_text.encode()) + padder.finalize()
        encryptor = Cipher(algorithms.AES(self._key), modes.CBC(iv)).encryptor()
        cipher_bytes = encryptor.update(padded) + encryptor.finalize()
        return f"{iv.hex()}:{cipher_bytes.hex()}"

    def decrypt(self, cipher_text: str) -> str:
        parts = cipher_text.split(":")
        if (
            len(parts) != 2
            or len(parts[0]) != IV_LENGTH * 2
            or not HEX_PATTERN.match(parts[0])
            or not HEX_PATTERN.match(parts[1])
        ):
            raise ValueError("Malformed ciphertext")
        iv, cipher_bytes = bytes.fromhex(parts[0]), bytes.fromhex(parts[1])
        decryptor = Cipher(algorithms.AES(self._key), modes.CBC(iv)).decryptor()
        padded = decryptor.update(cipher_bytes) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode()


class JwtTokenSigner:
    def __init__(
        self,
        access_secret: str,
        refresh_secret: str,
        access_expires: str,
        refresh_expires: str,
    ):
        self._access_secret = access_secret
        self._refresh_secret = refresh_secret
        self._access_ttl = parse_duration(access_expires)
        self._refresh_ttl = parse_duration(refresh_expires)

    def _sign(self, payload: dict[str, Any], secret: str, ttl: timedelta) -> str:
        now = datetime.now(timezone.utc)
        claims = dict(payload)
        claims.update({"iat": now, "exp": now + ttl, "jti": uuid.uuid4().hex})
        return jwt.encode(claims, secret, algorithm="HS256")

    def sign_access_token(self, payload: dict[str, Any]) -> str:
        return self._sign(payload, self._access_secret, self._access_ttl)

    def sign_refresh_token(self, payload: dict[str, Any]) -> str:
        return self._sign(payload, self._refresh_secret, self._refresh_ttl)

    def verify_access_token(self, token: str) -> dict[str, Any]:
        return jwt.decode(token, self._access_secret, algorithms=["HS256"])

    def verify_refresh_token(self, token: str) -> dict[str, Any]:
        return jwt.decode(token, self._refresh_secret, algorithms=["HS256"])
