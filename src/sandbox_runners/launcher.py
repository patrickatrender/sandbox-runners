"""Start a sandbox per queued job, stop it when the job ends, and sweep leftovers.

A runner and its sandbox share one name, ``gha-<installation>-<repo>-<job>``.
The name carries everything the sweep needs to find the runner on GitHub, so
the controller keeps no state of its own.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from render.sandbox import SandboxClient, SandboxExecExit, SandboxExecOutput

from sandbox_runners.config import Settings
from sandbox_runners.github import GitHubApp

log = logging.getLogger(__name__)

PREFIX = "gha-"
NAME_RE = re.compile(r"^gha-(\d+)-(\d+)-(\d+)$")
DONE = {"terminated", "errored"}
# A runner that has just finished can take a moment to deregister.
DEREGISTER_GRACE_SECONDS = 120


@dataclass(frozen=True)
class QueuedJob:
    installation_id: int
    repo_id: int
    repo: str
    job_id: int
    labels: list[str]


def runner_name(installation_id: int, repo_id: int, job_id: int) -> str:
    return f"{PREFIX}{installation_id}-{repo_id}-{job_id}"


def parse_name(name: str | None) -> tuple[int, int, int] | None:
    m = NAME_RE.match(name or "")
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def runner_labels(job_labels: list[str], settings: Settings) -> list[str] | None:
    """The labels to register a runner with, or None if the job isn't ours."""
    wanted = {label.lower() for label in job_labels}
    if settings.runner_label.lower() not in wanted:
        return None
    if not wanted <= {label.lower() for label in settings.allowed_labels}:
        return None
    return sorted(settings.allowed_labels)


def start_command(jitconfig: str) -> str:
    # The config is base64, so it's safe inside single quotes. setsid detaches
    # the runner, so it outlives this exec call.
    return (
        "install -d -m 700 /run/runner && "
        f"printf %s '{jitconfig}' > /run/runner/jitconfig && "
        "setsid -f /usr/local/bin/start-runner >/var/log/start-runner.log 2>&1 < /dev/null"
    )


class Launcher:
    def __init__(self, settings: Settings, github: GitHubApp, sandboxes: SandboxClient):
        self.settings = settings
        self.github = github
        self.sandboxes = sandboxes

    async def _live_sandboxes(self, name: str | None = None):
        cursor = None
        while True:
            page = await self.sandboxes.list(
                name=name,
                sandbox_group_id=self.settings.sandbox_group_id,
                cursor=cursor,
                limit=100,
            )
            for sb in page.sandboxes:
                if sb.status not in DONE and (sb.name or "").startswith(PREFIX):
                    yield sb
            if not page.next_cursor:
                return
            cursor = page.next_cursor

    async def launch(self, job: QueuedJob) -> str | None:
        labels = runner_labels(job.labels, self.settings)
        if labels is None:
            return None
        name = runner_name(job.installation_id, job.repo_id, job.job_id)
        async for _ in self._live_sandboxes(name):
            log.info("sandbox %s already exists; ignoring redelivered webhook", name)
            return None

        runner_id, jitconfig = await self.github.generate_jitconfig(
            job.installation_id, job.repo, name, labels
        )
        sandbox_id = None
        try:
            sb = await self.sandboxes.create(
                name=name,
                image=self.settings.sandbox_image,
                plan=self.settings.sandbox_plan,
                sandbox_group_id=self.settings.sandbox_group_id,
                timeout_seconds=self.settings.job_timeout_seconds,
                # The runner runs detached, not inside an exec, so idle
                # suspension could pause a job. The lifetime cap and the sweep
                # bound runaway sandboxes instead.
                idle_timeout_seconds=0,
                wait=True,
            )
            sandbox_id = sb.id
            exit_code, output = await self._exec(sb.id, start_command(jitconfig))
            if exit_code != 0:
                raise RuntimeError(f"start-runner exited {exit_code}: {output[-500:]}")
        except Exception:
            log.exception("failed to start runner %s", name)
            await self._cleanup(job.installation_id, job.repo, runner_id, sandbox_id)
            raise
        log.info("started runner %s in sandbox %s for %s", name, sandbox_id, job.repo)
        return sandbox_id

    async def _exec(self, sandbox_id: str, command: str) -> tuple[int, str]:
        out: list[str] = []
        async for event in self.sandboxes.exec(sandbox_id, command):
            if isinstance(event, SandboxExecOutput):
                out.append(event.data)
            elif isinstance(event, SandboxExecExit):
                return event.exit_code, "".join(out)
        return -1, "".join(out)

    async def _cleanup(
        self, installation_id: int, repo: str, runner_id: int | None, sandbox_id: str | None
    ) -> None:
        if sandbox_id:
            try:
                await self.sandboxes.terminate(sandbox_id)
            except Exception:
                log.exception("failed to terminate sandbox %s", sandbox_id)
        if runner_id is not None:
            try:
                await self.github.delete_runner(installation_id, repo, runner_id)
            except Exception:
                log.exception("failed to delete runner %s on %s", runner_id, repo)

    async def finish(self, runner: str | None) -> bool:
        """Terminate the sandbox for a runner whose job completed."""
        if not parse_name(runner):
            return False
        stopped = False
        async for sb in self._live_sandboxes(runner):
            await self.sandboxes.terminate(sb.id)
            log.info("terminated sandbox %s for finished runner %s", sb.id, runner)
            stopped = True
        return stopped

    async def sweep(self, now: datetime | None = None) -> list[str]:
        """Terminate sandboxes whose runner finished or never got a job.

        Covers missed webhooks, cancelled jobs whose runner never ran, and
        runners that failed to start.
        """
        now = now or datetime.now(UTC)
        by_repo: dict[tuple[int, int], list] = {}
        async for sb in self._live_sandboxes():
            parsed = parse_name(sb.name)
            if parsed:
                by_repo.setdefault(parsed[:2], []).append(sb)

        terminated: list[str] = []
        for (installation_id, repo_id), sandboxes in by_repo.items():
            try:
                repo = await self.github.repo_name(installation_id, repo_id)
                runners = await self.github.list_runners(installation_id, repo)
            except Exception:
                log.exception("sweep: can't read runners for repo %s", repo_id)
                continue
            for sb in sandboxes:
                age = (now - sb.created_at).total_seconds()
                runner = runners.get(sb.name)
                if runner is None and age > DEREGISTER_GRACE_SECONDS:
                    reason = "runner is gone"
                elif runner and not runner.busy and age > self.settings.unclaimed_timeout_seconds:
                    reason = "runner never picked up a job"
                    await self.github.delete_runner(installation_id, repo, runner.id)
                else:
                    continue
                await self.sandboxes.terminate(sb.id)
                log.info("sweep: terminated %s (%s): %s", sb.id, sb.name, reason)
                terminated.append(sb.id)
        return terminated
