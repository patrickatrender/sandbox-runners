from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from render.sandbox import Sandbox, SandboxExecExit, SandboxExecOutput, SandboxList

from sandbox_runners.config import Settings
from sandbox_runners.github import Runner
from sandbox_runners.launcher import (
    Launcher,
    QueuedJob,
    parse_name,
    runner_labels,
    runner_name,
    start_command,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
SETTINGS = Settings(github_app_id="1", github_app_private_key="k", github_webhook_secret="s")


def sandbox(name: str, status: str = "running", age: int = 0, id: str | None = None) -> Sandbox:
    return Sandbox(
        id=id or f"sbx-{name}",
        status=status,
        plan="2c-4g",
        network_policy=None,
        region="oregon",
        timeout_seconds=0,
        idle_timeout_seconds=0,
        created_at=NOW - timedelta(seconds=age),
        terminated_at=None,
        sandbox_group_id=None,
        name=name,
    )


class FakeSandboxes:
    def __init__(self, existing: list[Sandbox] | None = None, exit_code: int = 0):
        self.existing = existing or []
        self.exit_code = exit_code
        self.created: list[dict] = []
        self.commands: list[str] = []
        self.terminated: list[str] = []

    async def list(self, *, name=None, sandbox_group_id=None, cursor=None, limit=None):
        found = [s for s in self.existing if name is None or s.name == name]
        return SandboxList(sandboxes=found, next_cursor=None)

    async def create(self, **kw):
        self.created.append(kw)
        return sandbox(kw["name"], id="sbx-new")

    async def exec(self, sandbox_id, command):
        self.commands.append(command)
        yield SandboxExecOutput(stream="stdout", data="ok")
        yield SandboxExecExit(exit_code=self.exit_code)

    async def terminate(self, sandbox_id):
        self.terminated.append(sandbox_id)


class FakeGitHub:
    def __init__(self, runners: dict[str, Runner] | None = None):
        self.runners = runners or {}
        self.registered: list[tuple] = []
        self.deleted: list[int] = []

    async def generate_jitconfig(self, installation_id, repo, name, labels):
        self.registered.append((installation_id, repo, name, labels))
        return 99, "ZW5jb2RlZA=="

    async def repo_name(self, installation_id, repo_id):
        return "acme/app"

    async def list_runners(self, installation_id, repo):
        return self.runners

    async def delete_runner(self, installation_id, repo, runner_id):
        self.deleted.append(runner_id)


def job(labels=("self-hosted", "render-sandbox")) -> QueuedJob:
    return QueuedJob(
        installation_id=7, repo_id=42, repo="acme/app", job_id=1000, labels=list(labels)
    )


def test_name_round_trip():
    assert parse_name(runner_name(7, 42, 1000)) == (7, 42, 1000)
    assert parse_name("gha-7-42") is None
    assert parse_name(None) is None
    assert parse_name("my-sandbox") is None


@pytest.mark.parametrize(
    ("labels", "ours"),
    [
        (["self-hosted", "render-sandbox"], True),
        (["Self-Hosted", "Render-Sandbox", "linux", "x64"], True),
        (["ubuntu-latest"], False),
        (["self-hosted"], False),
        (["self-hosted", "render-sandbox", "gpu"], False),
    ],
)
def test_runner_labels(labels, ours):
    assert (runner_labels(labels, SETTINGS) is not None) is ours


def test_start_command_keeps_config_off_the_command_line_of_the_runner():
    cmd = start_command("ZW5jb2RlZA==")
    assert "printf %s 'ZW5jb2RlZA==' > /run/runner/jitconfig" in cmd
    assert "setsid -f /usr/local/bin/start-runner" in cmd


async def test_launch_creates_sandbox_and_starts_runner():
    sbx, gh = FakeSandboxes(), FakeGitHub()
    sandbox_id = await Launcher(SETTINGS, gh, sbx).launch(job())

    assert sandbox_id == "sbx-new"
    assert gh.registered[0][:3] == (7, "acme/app", "gha-7-42-1000")
    created = sbx.created[0]
    assert created["name"] == "gha-7-42-1000"
    assert created["image"] == "ci-runner"
    assert created["idle_timeout_seconds"] == 0
    assert created["timeout_seconds"] == 6 * 60 * 60
    assert "ZW5jb2RlZA==" in sbx.commands[0]


async def test_launch_ignores_jobs_for_other_runners():
    sbx, gh = FakeSandboxes(), FakeGitHub()
    assert await Launcher(SETTINGS, gh, sbx).launch(job(["ubuntu-latest"])) is None
    assert gh.registered == [] and sbx.created == []


async def test_launch_ignores_redelivered_webhook():
    sbx = FakeSandboxes(existing=[sandbox("gha-7-42-1000")])
    gh = FakeGitHub()
    assert await Launcher(SETTINGS, gh, sbx).launch(job()) is None
    assert gh.registered == [] and sbx.created == []


async def test_launch_relaunches_after_a_terminated_sandbox():
    sbx = FakeSandboxes(existing=[sandbox("gha-7-42-1000", status="terminated")])
    assert await Launcher(SETTINGS, FakeGitHub(), sbx).launch(job()) == "sbx-new"


async def test_launch_cleans_up_when_runner_fails_to_start():
    sbx, gh = FakeSandboxes(exit_code=1), FakeGitHub()
    with pytest.raises(RuntimeError, match="start-runner exited 1"):
        await Launcher(SETTINGS, gh, sbx).launch(job())
    assert sbx.terminated == ["sbx-new"]
    assert gh.deleted == [99]


async def test_finish_terminates_matching_sandbox():
    sbx = FakeSandboxes(existing=[sandbox("gha-7-42-1000"), sandbox("gha-7-42-2000")])
    assert await Launcher(SETTINGS, FakeGitHub(), sbx).finish("gha-7-42-1000")
    assert sbx.terminated == ["sbx-gha-7-42-1000"]


async def test_finish_ignores_runners_we_did_not_start():
    sbx = FakeSandboxes(existing=[sandbox("gha-7-42-1000")])
    assert not await Launcher(SETTINGS, FakeGitHub(), sbx).finish("GitHub Actions 3")
    assert not await Launcher(SETTINGS, FakeGitHub(), sbx).finish(None)
    assert sbx.terminated == []


async def test_sweep():
    sbx = FakeSandboxes(
        existing=[
            sandbox("gha-7-42-1", age=600),  # runner gone: job finished, webhook missed
            sandbox("gha-7-42-2", age=30),  # runner gone but just created: keep
            sandbox("gha-7-42-3", age=3600),  # idle past the unclaimed timeout
            sandbox("gha-7-42-4", age=3600),  # busy: keep
            sandbox("gha-7-42-5", age=60),  # idle but recent: keep
            sandbox("gha-7-42-6", age=600, status="terminated"),  # already done
            sandbox("someone-else", age=9999),  # not ours
        ]
    )
    gh = FakeGitHub(
        runners={
            "gha-7-42-3": Runner(3, "gha-7-42-3", "online", busy=False),
            "gha-7-42-4": Runner(4, "gha-7-42-4", "online", busy=True),
            "gha-7-42-5": Runner(5, "gha-7-42-5", "online", busy=False),
        }
    )
    terminated = await Launcher(SETTINGS, gh, sbx).sweep(now=NOW)
    assert terminated == ["sbx-gha-7-42-1", "sbx-gha-7-42-3"]
    assert gh.deleted == [3]
