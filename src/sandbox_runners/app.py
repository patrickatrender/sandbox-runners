"""Web service that receives GitHub `workflow_job` webhooks."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
from collections.abc import AsyncIterator

from render import RenderAsync
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from sandbox_runners.config import Settings
from sandbox_runners.github import GitHubApp
from sandbox_runners.launcher import Launcher, QueuedJob

log = logging.getLogger(__name__)


def valid_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


async def _run_logged(coro) -> None:
    try:
        await coro
    except Exception:
        log.exception("background task failed")


def create_app(settings: Settings | None = None, launcher: Launcher | None = None) -> Starlette:
    settings = settings or Settings.from_env()

    async def webhook(request: Request) -> PlainTextResponse:
        body = await request.body()
        if not valid_signature(
            settings.github_webhook_secret, body, request.headers.get("X-Hub-Signature-256")
        ):
            return PlainTextResponse("bad signature", status_code=401)
        if request.headers.get("X-GitHub-Event") != "workflow_job":
            return PlainTextResponse("ignored", status_code=202)

        event = json.loads(body)
        job = event["workflow_job"]
        lc: Launcher = request.app.state.launcher
        task = None
        if event["action"] == "queued":
            task = lc.launch(
                QueuedJob(
                    installation_id=event["installation"]["id"],
                    repo_id=event["repository"]["id"],
                    repo=event["repository"]["full_name"],
                    job_id=job["id"],
                    labels=job["labels"],
                )
            )
        elif event["action"] == "completed":
            task = lc.finish(job.get("runner_name"))
        # GitHub expects a reply within 10 seconds; starting a sandbox takes
        # longer, so do the work after responding.
        background = BackgroundTask(_run_logged, task) if task else None
        return PlainTextResponse("accepted", status_code=202, background=background)

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def sweep_forever(lc: Launcher) -> None:
        while True:
            await asyncio.sleep(settings.sweep_interval_seconds)
            await _run_logged(lc.sweep())

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        lc = launcher or Launcher(
            settings,
            GitHubApp(settings.github_app_id, settings.github_app_private_key),
            RenderAsync().sandboxes,
        )
        app.state.launcher = lc
        sweeper = asyncio.create_task(sweep_forever(lc))
        try:
            yield
        finally:
            sweeper.cancel()

    return Starlette(
        routes=[
            Route("/webhook", webhook, methods=["POST"]),
            Route("/healthz", healthz),
        ],
        lifespan=lifespan,
    )


def app() -> Starlette:
    """Factory for `uvicorn --factory sandbox_runners.app:app`."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    return create_app()
