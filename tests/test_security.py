import pytest

from outly.adapters.security import AesCredentialCipher, JwtTokenSigner, parse_duration

KEY = "ab" * 32


def test_encrypt_roundtrip():
    cipher = AesCredentialCipher(KEY)
    encrypted = cipher.encrypt("secret app password")
    iv_hex, payload_hex = encrypted.split(":")
    assert len(iv_hex) == 32
    assert cipher.decrypt(encrypted) == "secret app password"


def test_encrypt_unique_iv():
    cipher = AesCredentialCipher(KEY)
    assert cipher.encrypt("same") != cipher.encrypt("same")


def test_decrypt_rejects_malformed():
    cipher = AesCredentialCipher(KEY)
    for bad in ("nope", "aa:bb:cc", "zz" * 16 + ":aabb", "aabb:aabb"):
        with pytest.raises(ValueError):
            cipher.decrypt(bad)


def test_invalid_key_rejected():
    with pytest.raises(ValueError):
        AesCredentialCipher("short")


def test_jwt_roundtrip():
    signer = JwtTokenSigner("access", "refresh", "15m", "30d")
    token = signer.sign_access_token({"id": "u1", "email": "a@b.c"})
    payload = signer.verify_access_token(token)
    assert payload["id"] == "u1"
    with pytest.raises(Exception):
        signer.verify_refresh_token(token)


def test_parse_duration():
    assert parse_duration("15m").total_seconds() == 900
    assert parse_duration("30d").days == 30
    with pytest.raises(ValueError):
        parse_duration("nope")
