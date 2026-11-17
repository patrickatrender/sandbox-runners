"""Import a runner image into a sandbox group and wait until it's ready.

    python -m sandbox_runners.import_image ghcr.io/owner/sandbox-runner@sha256:...

Needs RENDER_API_KEY and RENDER_WORKSPACE_ID. Set SANDBOX_GROUP_ID to import
into a specific group; otherwise the workspace's default group is used. For a
private registry, set REGISTRY_USERNAME and REGISTRY_PASSWORD.
"""

from __future__ import annotations

import argparse
import asyncio
import os

from render import RenderAsync
from render.sandbox import SandboxClient


async def default_group_id(sandboxes: SandboxClient) -> str:
    cursor = None
    while True:
        page = await sandboxes.list_groups(cursor=cursor)
        for group in page.groups:
            if group.is_default:
                return group.id
        if not page.next_cursor:
            raise RuntimeError("workspace has no default sandbox group")
        cursor = page.next_cursor


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="OCI image by tag or digest (linux/amd64)")
    parser.add_argument("--name", default=os.environ.get("SANDBOX_IMAGE", "ci-runner"))
    args = parser.parse_args()

    sandboxes = RenderAsync().sandboxes
    group_id = os.environ.get("SANDBOX_GROUP_ID") or await default_group_id(sandboxes)
    creds = None
    if os.environ.get("REGISTRY_USERNAME"):
        creds = {
            "username": os.environ["REGISTRY_USERNAME"],
            "password": os.environ["REGISTRY_PASSWORD"],
        }

    image = await sandboxes.images.import_(
        sandbox_group_id=group_id, name=args.name, source=args.source, registry_credentials=creds
    )
    print(f"importing {args.source} as {args.name} ({image.id}) into {group_id}", flush=True)
    image = await sandboxes.images.wait_until_ready(
        sandbox_group_id=group_id, image_id=image.id, timeout_seconds=30 * 60
    )
    print(f"ready: {image.id} {image.source_digest}")


if __name__ == "__main__":
    asyncio.run(main())
