from __future__ import annotations

import hashlib
import hmac
import json

from starlette.testclient import TestClient

from sandbox_runners.app import create_app, valid_signature
from sandbox_runners.config import Settings

SECRET = "webhook-secret"
SETTINGS = Settings(
    github_app_id="1",
    github_app_private_key="k",
    github_webhook_secret=SECRET,
    sweep_interval_seconds=3600,
)


class RecordingLauncher:
    def __init__(self):
        self.launched = []
        self.finished = []

    async def launch(self, job):
        self.launched.append(job)

    async def finish(self, runner):
        self.finished.append(runner)

    async def sweep(self):
        return []


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def post(client, payload, event="workflow_job", signature=None):
    body = json.dumps(payload).encode()
    return client.post(
        "/webhook",
        content=body,
        headers={"X-GitHub-Event": event, "X-Hub-Signature-256": signature or sign(body)},
    )


def payload(action, runner_name=None):
    return {
        "action": action,
        "installation": {"id": 7},
        "repository": {"id": 42, "full_name": "acme/app"},
        "workflow_job": {
            "id": 1000,
            "labels": ["self-hosted", "render-sandbox"],
            "runner_name": runner_name,
        },
    }


def test_valid_signature():
    assert valid_signature(SECRET, b"x", sign(b"x"))
    assert not valid_signature(SECRET, b"x", sign(b"y"))
    assert not valid_signature(SECRET, b"x", None)
    assert not valid_signature(SECRET, b"x", "sha1=abc")


def test_webhook_routes_events():
    launcher = RecordingLauncher()
    with TestClient(create_app(SETTINGS, launcher)) as client:
        assert client.get("/healthz").json() == {"ok": True}
        assert post(client, payload("queued")).status_code == 202
        assert post(client, payload("in_progress", "gha-7-42-1000")).status_code == 202
        assert post(client, payload("completed", "gha-7-42-1000")).status_code == 202
        assert post(client, {"zen": "hi"}, event="ping").status_code == 202

    assert len(launcher.launched) == 1
    job = launcher.launched[0]
    assert (job.installation_id, job.repo_id, job.repo, job.job_id) == (7, 42, "acme/app", 1000)
    assert launcher.finished == ["gha-7-42-1000"]


def test_webhook_rejects_bad_signature():
    launcher = RecordingLauncher()
    with TestClient(create_app(SETTINGS, launcher)) as client:
        resp = post(client, payload("queued"), signature="sha256=00")
    assert resp.status_code == 401
    assert launcher.launched == []
