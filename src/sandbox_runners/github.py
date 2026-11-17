"""The GitHub App calls the controller needs."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

import httpx
import jwt

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


@dataclass(frozen=True)
class Runner:
    id: int
    name: str
    status: str  # online or offline
    busy: bool


class GitHubApp:
    def __init__(self, app_id: str, private_key: str, http: httpx.AsyncClient | None = None):
        self._app_id = app_id
        self._private_key = private_key
        self._http = http or httpx.AsyncClient(base_url=API, headers=HEADERS, timeout=30)
        self._tokens: dict[int, tuple[str, float]] = {}

    def _app_jwt(self) -> str:
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + 540, "iss": self._app_id}
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def _token(self, installation_id: int) -> str:
        cached = self._tokens.get(installation_id)
        if cached and cached[1] > time.time() + 300:
            return cached[0]
        resp = await self._http.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {self._app_jwt()}"},
        )
        resp.raise_for_status()
        body = resp.json()
        expires = datetime.fromisoformat(body["expires_at"]).timestamp()
        self._tokens[installation_id] = (body["token"], expires)
        return body["token"]

    async def _request(self, installation_id: int, method: str, path: str, **kw) -> httpx.Response:
        token = await self._token(installation_id)
        resp = await self._http.request(
            method, path, headers={"Authorization": f"Bearer {token}"}, **kw
        )
        resp.raise_for_status()
        return resp

    async def generate_jitconfig(
        self, installation_id: int, repo: str, name: str, labels: list[str]
    ) -> tuple[int, str]:
        """Register a one-job runner on repo. Returns its id and encoded config."""
        resp = await self._request(
            installation_id,
            "POST",
            f"/repos/{repo}/actions/runners/generate-jitconfig",
            json={"name": name, "runner_group_id": 1, "labels": labels, "work_folder": "_work"},
        )
        body = resp.json()
        return body["runner"]["id"], body["encoded_jit_config"]

    async def repo_name(self, installation_id: int, repo_id: int) -> str:
        resp = await self._request(installation_id, "GET", f"/repositories/{repo_id}")
        return resp.json()["full_name"]

    async def list_runners(self, installation_id: int, repo: str) -> dict[str, Runner]:
        runners: dict[str, Runner] = {}
        page = 1
        while True:
            resp = await self._request(
                installation_id,
                "GET",
                f"/repos/{repo}/actions/runners",
                params={"per_page": 100, "page": page},
            )
            batch = resp.json()["runners"]
            for r in batch:
                runners[r["name"]] = Runner(r["id"], r["name"], r["status"], r["busy"])
            if len(batch) < 100:
                return runners
            page += 1

    async def delete_runner(self, installation_id: int, repo: str, runner_id: int) -> None:
        token = await self._token(installation_id)
        resp = await self._http.delete(
            f"/repos/{repo}/actions/runners/{runner_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 404:
            resp.raise_for_status()
