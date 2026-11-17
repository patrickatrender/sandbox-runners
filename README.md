# sandbox-runners

Runs GitHub Actions jobs in [Render sandboxes](https://render.com), one sandbox per job.

A Render web service receives GitHub's `workflow_job` webhooks. For each queued job that
asks for the `render-sandbox` label, it:

1. Registers a one-job (JIT) runner through a GitHub App.
2. Starts a sandbox from the `ci-runner` image, which has Docker and the Actions runner.
3. Starts the runner in the sandbox. The runner picks up a job, runs it and exits.

When the job completes, the service terminates the sandbox. A sweep every 5 minutes removes
sandboxes whose job ended without a webhook, and runners that never got a job.

Workflows opt in with:

```yaml
runs-on: [self-hosted, render-sandbox]
```

A job that asks for any other custom label is left alone. GitHub routes jobs by label only,
so these sandboxes don't absorb overflow from GitHub-hosted runners.

## Sandboxes

- Plan `2c-4g`, set with `SANDBOX_PLAN`.
- Idle suspension is off (`idle_timeout_seconds=0`). The lifetime cap is 6 hours, set with
  `JOB_TIMEOUT_SECONDS`.
- Jobs run as the `runner` user with passwordless sudo, as on GitHub-hosted runners.
- Docker works, including builds and service containers. Nested virtualization (`/dev/kvm`)
  does not.
- To cap concurrent jobs, set `concurrency_limit` on the sandbox group in `SANDBOX_GROUP_ID`.

## Setup

### 1. Runner image

`image/` holds the Dockerfile. The **Runner image** workflow builds it, pushes it to
`ghcr.io/<owner>/sandbox-runner`, and imports it into Render as `ci-runner`. The import step
runs once these are set on the repository:

| Kind | Name | Value |
|---|---|---|
| Secret | `RENDER_API_KEY` | Render API key |
| Variable | `RENDER_WORKSPACE_ID` | Workspace ID (`tea-...`) |
| Variable | `SANDBOX_GROUP_ID` | Optional; defaults to the workspace's default group |
| Secret | `GHCR_READ_USERNAME`, `GHCR_READ_TOKEN` | Only while the GHCR package is private |

To import by hand:

```sh
RENDER_API_KEY=... RENDER_WORKSPACE_ID=... \
  uv run python -m sandbox_runners.import_image ghcr.io/<owner>/sandbox-runner:latest
```

### 2. GitHub App

Create a GitHub App with:

- **Repository permissions:** Administration: read and write (to register runners),
  Actions: read, Metadata: read.
- **Webhook:** URL `https://<service>.onrender.com/webhook` and a secret. Subscribe to
  **Workflow job**.

Install it on the repositories that should use sandboxes, then generate a private key.

### 3. Service

Deploy with `render.yaml` (Blueprint) and set:

| Variable | Value |
|---|---|
| `RENDER_API_KEY`, `RENDER_WORKSPACE_ID` | Render credentials |
| `GITHUB_APP_ID` | The App's ID |
| `GITHUB_APP_PRIVATE_KEY` | The PEM, with newlines as `\n` |
| `GITHUB_WEBHOOK_SECRET` | The App's webhook secret |
| `SANDBOX_GROUP_ID` | Optional sandbox group |

## Development

```sh
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

## Not yet verified

- An end-to-end job through a real GitHub App.
- Runners are registered per repository. Organization-level runners would need the
  organization Self-hosted runners permission instead.
