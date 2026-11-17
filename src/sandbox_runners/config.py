"""Settings, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


@dataclass(frozen=True)
class Settings:
    github_app_id: str
    github_app_private_key: str
    github_webhook_secret: str
    # The label that routes a job to Render sandboxes, as in
    # `runs-on: [self-hosted, render-sandbox]`.
    runner_label: str = "render-sandbox"
    # Labels a runner may also carry. A job that asks for any other label
    # (for example `gpu`) is left for another runner.
    extra_labels: frozenset[str] = field(
        default_factory=lambda: frozenset({"self-hosted", "linux", "x64"})
    )
    sandbox_image: str = "ci-runner"
    sandbox_plan: str = "2c-4g"
    sandbox_group_id: str | None = None
    # Hard cap on a runner sandbox's lifetime, enforced by Render.
    job_timeout_seconds: int = 6 * 60 * 60
    # A runner that has not picked up a job after this long is removed.
    unclaimed_timeout_seconds: int = 20 * 60
    sweep_interval_seconds: int = 5 * 60

    @property
    def allowed_labels(self) -> frozenset[str]:
        return self.extra_labels | {self.runner_label}

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ
        return cls(
            github_app_id=_required("GITHUB_APP_ID"),
            # Render env vars can't hold literal newlines easily, so accept "\n".
            github_app_private_key=_required("GITHUB_APP_PRIVATE_KEY").replace("\\n", "\n"),
            github_webhook_secret=_required("GITHUB_WEBHOOK_SECRET"),
            runner_label=env.get("RUNNER_LABEL", "render-sandbox"),
            sandbox_image=env.get("SANDBOX_IMAGE", "ci-runner"),
            sandbox_plan=env.get("SANDBOX_PLAN", "2c-4g"),
            sandbox_group_id=env.get("SANDBOX_GROUP_ID") or None,
            job_timeout_seconds=int(env.get("JOB_TIMEOUT_SECONDS", 6 * 60 * 60)),
            unclaimed_timeout_seconds=int(env.get("UNCLAIMED_TIMEOUT_SECONDS", 20 * 60)),
            sweep_interval_seconds=int(env.get("SWEEP_INTERVAL_SECONDS", 5 * 60)),
        )
