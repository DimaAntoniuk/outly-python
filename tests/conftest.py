import pytest
from fastapi.testclient import TestClient

from outly.api.app import create_app
from outly.config import Settings


class FakeQueue:
    def __init__(self):
        self.jobs: list[tuple[str, int]] = []

    async def enqueue_send(self, email_job_id: str, delay_ms: int = 0) -> None:
        self.jobs.append((email_job_id, delay_ms))


class FakeMailer:
    def __init__(self, verify_ok: bool = True, send_error: Exception | None = None):
        self.verify_ok = verify_ok
        self.send_error = send_error
        self.sent: list[dict] = []

    async def verify_credentials(self, host, port, email, password) -> bool:
        return self.verify_ok

    async def send(self, **kwargs) -> None:
        if self.send_error:
            raise self.send_error
        self.sent.append(kwargs)


class FakeGoogleVerifier:
    def __init__(self, payload: dict | None = None):
        self.payload = payload

    async def verify(self, id_token: str) -> dict | None:
        return self.payload


GOOGLE_PAYLOAD = {
    "sub": "google-123",
    "email": "jobseeker@example.com",
    "name": "Job Seeker",
    "picture": "https://example.com/avatar.png",
}


@pytest.fixture
def queue():
    return FakeQueue()


@pytest.fixture
def mailer():
    return FakeMailer()


@pytest.fixture
def client(tmp_path, queue, mailer):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
        attachment_dir=str(tmp_path / "attachments"),
        tracking_base_url="http://testserver",
        server_base_url="http://testserver",
    )
    app = create_app(settings=settings, queue=queue)
    with TestClient(app) as test_client:
        app.state.google_verifier = FakeGoogleVerifier(GOOGLE_PAYLOAD)
        app.state.mailer = mailer
        yield test_client


@pytest.fixture
def auth_headers(client):
    response = client.post("/auth/google", json={"idToken": "fake-token"})
    assert response.status_code == 200, response.text
    token = response.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def verified_sender(client, auth_headers):
    response = client.post(
        "/senders/",
        json={
            "name": "Main Sender",
            "email": "sender@example.com",
            "appPassword": "app-password-123",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()
