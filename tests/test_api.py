from datetime import datetime, timezone


def _campaign_payload(sender_id: str, **overrides):
    payload = {
        "senderIds": [sender_id],
        "subject": "Hello {{name}}",
        "body": "<p>Hi {{name}}</p>",
        "startTime": datetime.now(timezone.utc).isoformat(),
        "delaySeconds": 5,
        "hourlyLimit": 60,
        "emails": [
            {"email": "alice@example.com", "columnData": {"name": "Alice"}},
            {"email": "bob@example.com", "columnData": {"name": "Bob"}},
            "ALICE@example.com",
        ],
    }
    payload.update(overrides)
    return payload


def test_home_route(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.text == "This is the home route"


def test_auth_requires_token(client):
    assert client.get("/users/").status_code == 401
    response = client.get("/users/", headers={"Authorization": "Token x"})
    assert response.json()["message"] == "Invalid authorization format"


def test_google_login_and_me(client, auth_headers):
    response = client.get("/users/", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["email"] == "jobseeker@example.com"


def test_refresh_rotation(client):
    login = client.post("/auth/google", json={"idToken": "fake"})
    cookie = login.cookies.get("refreshToken")
    assert cookie
    client.cookies.set("refreshToken", cookie, path="/auth/refresh")
    first = client.post("/auth/refresh")
    assert first.status_code == 200
    second = client.post("/auth/refresh")
    assert second.status_code == 401
    assert second.json()["message"] == "Token revoked"


def test_create_sender_validation(client, auth_headers):
    response = client.post("/senders/", json={"name": "x"}, headers=auth_headers)
    assert response.status_code == 400
    assert "Missing required fields" in response.json()["message"]

    response = client.post(
        "/senders/",
        json={"name": "x", "email": "not-an-email", "appPassword": "pw"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["message"] == "Invalid email format"


def test_create_sender_rejects_bad_credentials(client, auth_headers, mailer):
    mailer.verify_ok = False
    response = client.post(
        "/senders/",
        json={"name": "x", "email": "a@b.co", "appPassword": "pw"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "Invalid SMTP credentials" in response.json()["message"]


def test_sender_lifecycle(client, auth_headers, verified_sender):
    assert verified_sender["isVerified"] is True
    assert "appPassword" not in verified_sender

    listed = client.get("/senders/", headers=auth_headers).json()
    emails = client.get("/senders/email", headers=auth_headers).json()
    assert "sender@example.com" in emails
    assert any(s["email"] == "sender@example.com" for s in listed)

    detail = client.get(f"/senders/{verified_sender['id']}", headers=auth_headers).json()
    assert detail["warmupStatus"] == "active"
    assert detail["currentDailyCount"] == 0
    assert detail["effectiveDailyLimit"] == 20

    duplicate = client.post(
        "/senders/",
        json={
            "name": "Dup",
            "email": "sender@example.com",
            "appPassword": "pw",
        },
        headers=auth_headers,
    )
    assert duplicate.status_code == 409


def test_template_crud(client, auth_headers):
    created = client.post(
        "/templates/",
        json={"name": "Intro", "subject": "s", "body": "b"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    template_id = created.json()["id"]

    conflict = client.post(
        "/templates/",
        json={"name": "Intro", "subject": "s2", "body": "b2"},
        headers=auth_headers,
    )
    assert conflict.status_code == 409

    updated = client.put(
        f"/templates/{template_id}", json={"subject": "new"}, headers=auth_headers
    )
    assert updated.json()["subject"] == "new"

    deleted = client.delete(f"/templates/{template_id}", headers=auth_headers)
    assert deleted.json()["message"] == "Template deleted"
    assert client.get("/templates/", headers=auth_headers).json() == []


def test_campaign_create_validation(client, auth_headers, verified_sender):
    bad = _campaign_payload(verified_sender["id"], emails=[])
    response = client.post("/campaigns/", json=bad, headers=auth_headers)
    assert response.status_code == 400
    assert response.json()["message"] == "At least one recipient email is required"

    bad = _campaign_payload(verified_sender["id"], hourlyLimit=0)
    response = client.post("/campaigns/", json=bad, headers=auth_headers)
    assert response.json()["message"] == "hourlyLimit must be a number > 0"


def test_campaign_lifecycle(client, auth_headers, verified_sender, queue):
    created = client.post(
        "/campaigns/",
        json=_campaign_payload(verified_sender["id"]),
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    campaign_id = created.json()["campaignId"]
    assert created.json()["senderPool"] == [verified_sender["id"]]
    assert len(queue.jobs) == 2

    detail = client.get(f"/campaigns/{campaign_id}", headers=auth_headers).json()
    assert detail["campaign"]["totalRecipients"] == 2
    assert detail["_count"]["PENDING"] == 2

    paused = client.patch(f"/campaigns/{campaign_id}/pause", headers=auth_headers)
    assert paused.json()["status"] == "PAUSED"

    resumed = client.patch(f"/campaigns/{campaign_id}/resume", headers=auth_headers)
    assert resumed.json()["status"] == "SENDING"

    cancelled = client.patch(f"/campaigns/{campaign_id}/cancel", headers=auth_headers)
    assert cancelled.json()["status"] == "CANCELLED"

    again = client.patch(f"/campaigns/{campaign_id}/pause", headers=auth_headers)
    assert again.status_code == 409

    scheduled = client.get("/emails/schedule", headers=auth_headers).json()
    assert scheduled == []


def test_campaign_with_sequence(client, auth_headers, verified_sender):
    payload = _campaign_payload(
        verified_sender["id"],
        steps=[{"subject": "Follow up", "body": "ping", "waitDays": 2}],
    )
    created = client.post("/campaigns/", json=payload, headers=auth_headers)
    assert created.status_code == 201, created.text
    campaign_id = created.json()["campaignId"]

    sequence = client.get(f"/campaigns/{campaign_id}/sequence/", headers=auth_headers).json()
    assert sequence["hasSequence"] is True
    assert len(sequence["steps"]) == 2
    assert len(sequence["recipients"]) == 2
    assert sequence["recipients"][0]["stepStatuses"][0]["status"] == "SCHEDULED"

    paused = client.patch(f"/campaigns/{campaign_id}/sequence/pause", headers=auth_headers)
    assert paused.json() == {"message": "Sequence paused", "count": 2}


def test_unowned_campaign_forbidden(client, auth_headers):
    assert client.get("/campaigns/missing", headers=auth_headers).status_code == 404


def test_tracking_pixel_and_click(client, auth_headers, verified_sender):
    created = client.post(
        "/campaigns/",
        json=_campaign_payload(verified_sender["id"]),
        headers=auth_headers,
    )
    campaign_id = created.json()["campaignId"]
    scheduled = client.get("/emails/schedule", headers=auth_headers).json()
    job_id = scheduled[0]["id"]

    pixel = client.get(f"/track/open/{job_id}")
    assert pixel.status_code == 200
    assert pixel.headers["content-type"] == "image/gif"

    redirect = client.get(
        f"/track/click/{job_id}",
        params={"url": "https%3A%2F%2Fexample.com"},
        follow_redirects=False,
    )
    assert redirect.status_code == 302

    missing_url = client.get(f"/track/click/{job_id}")
    assert missing_url.status_code == 400

    unknown = client.get("/track/open/nonexistent")
    assert unknown.status_code == 200

    metrics = client.get(f"/api/tracking/campaigns/{campaign_id}", headers=auth_headers).json()
    assert metrics["totalSent"] == 0
    assert metrics["uniqueOpens"] >= 0


def test_email_search_validation(client, auth_headers):
    too_long = client.get(
        "/emails/search", params={"q": "x" * 201}, headers=auth_headers
    )
    assert too_long.status_code == 400

    bad_status = client.get(
        "/emails/search", params={"status": "NOPE"}, headers=auth_headers
    )
    assert bad_status.status_code == 400

    ok = client.get("/emails/search", params={"q": "alice"}, headers=auth_headers)
    assert ok.status_code == 200


def test_dev_login(client):
    response = client.post(
        "/auth/dev-login", json={"email": "Dev@Example.com", "name": "Dev User"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user"]["email"] == "dev@example.com"
    headers = {"Authorization": f"Bearer {data['accessToken']}"}
    me = client.get("/users/", headers=headers)
    assert me.status_code == 200
    assert me.json()["name"] == "Dev User"

    missing_email = client.post("/auth/dev-login", json={})
    assert missing_email.status_code == 400


def test_dev_login_absent_in_production(tmp_path, queue):
    from fastapi.testclient import TestClient

    from outly.api.app import create_app
    from outly.config import Settings

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/prod.db",
        attachment_dir=str(tmp_path / "attachments"),
        env="production",
    )
    app = create_app(settings=settings, queue=queue)
    with TestClient(app) as prod_client:
        response = prod_client.post("/auth/dev-login", json={"email": "a@b.co"})
        assert response.status_code == 404
