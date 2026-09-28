"""Deploy Khatti to Nebius Serverless with the official SDK (ai.v1 Endpoints and Jobs).

    python -m deploy.nebius_deploy endpoint reader-omni --project <id> --image <ref> [--dry-run]
    python -m deploy.nebius_deploy start reader-omni --project <id>     # "wake the NVIDIA reader"
    python -m deploy.nebius_deploy stop reader-omni --project <id>      # no GPU charge while stopped
    python -m deploy.nebius_deploy status --project <id>
    python -m deploy.nebius_deploy job jobs.eval --project <id> --image <ref> -- --split test --run-name v1

Auth comes from the Nebius CLI profile or NEBIUS_IAM_TOKEN (see the SDK docs). Request shapes
follow the ai.v1 API schema; --dry-run asks the API to validate a create without doing it.
Platform/preset names differ by region: check them with `nebius compute platform list`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

GIB = 1024**3


@dataclass(frozen=True)
class Target:
    name: str
    platform: str
    preset: str
    port: int | None = 8000
    command: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)
    disk_gib: int = 0
    public_ip: bool = True
    auth_token_env: str | None = None  # endpoint-level bearer token, read from this env var
    env_keys: tuple[str, ...] = ()  # env vars forwarded from the local environment / env file


APP_ENV = (
    "KHATTI_TOKEN_FACTORY_URL", "KHATTI_TOKEN_FACTORY_KEY", "KHATTI_FAST_MODEL", "KHATTI_STRUCTURER_MODEL",
    "KHATTI_REVIEWER_MODEL", "KHATTI_READERS", "KHATTI_PRIMARY_READERS", "KHATTI_CALIBRATOR_PATH", "KHATTI_TAU_DOC",
    "KHATTI_DATABASE_URL", "KHATTI_DATA_KEY", "KHATTI_JWT_SECRET", "KHATTI_RETENTION_DAYS", "KHATTI_S3_BUCKET",
    "KHATTI_S3_ENDPOINT_URL", "KHATTI_S3_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "KHATTI_PUBLIC_BASE_URL",
    "KHATTI_RATE_LIMIT_PER_MINUTE", "KHATTI_WEBHOOK_ALLOWED_HOSTS", "MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_USERNAME",
    "MLFLOW_TRACKING_PASSWORD",
)

TARGETS = {
    "api": Target("khatti-api", "cpu-e2", "2vcpu-8gb", env_keys=APP_ENV),
    "worker": Target("khatti-worker", "cpu-e2", "4vcpu-16gb", port=None, command=["python", "-m", "khatti.worker"],
                     public_ip=False, env_keys=APP_ENV),
    # One L40S 48GB for Nemotron 3 Nano Omni FP8; weights (~32 GB) need a bigger boot disk.
    "reader-omni": Target("khatti-reader-omni", "gpu-l40s-a", "1gpu-8vcpu-32gb", disk_gib=120,
                          auth_token_env="READER_OMNI_TOKEN", env_keys=("HF_TOKEN", "VLLM_API_KEY", "MODEL_ID")),
}


def load_env_file(path: Path | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if path and path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def _env(keys: tuple[str, ...], env: dict[str, str]):
    from nebius.api.nebius.ai.v1 import EndpointSpec__EnvironmentVariable as Var

    return [Var(name=k, value=env[k]) for k in keys if env.get(k)]


def endpoint_spec(t: Target, image: str, env: dict[str, str], platform: str | None = None, preset: str | None = None):
    from nebius.api.nebius.ai.v1 import EndpointSpec, EndpointSpec__DiskSpec, EndpointSpec__Port
    from nebius.api.nebius.ai.v1 import EndpointSpec__Port__Protocol as Protocol
    from nebius.api.nebius.compute.v1 import DiskSpec__DiskType

    kwargs = dict(
        image=image,
        platform=platform or t.platform,
        preset=preset or t.preset,
        environment_variables=_env(t.env_keys, env),
        public_ip=t.public_ip,
    )
    if t.port:
        kwargs["ports"] = [EndpointSpec__Port(container_port=t.port, protocol=Protocol.HTTP)]
    if t.command:
        # Both are single strings in the API (not lists).
        kwargs["container_command"] = t.command[0]
        kwargs["args"] = shlex.join(t.command[1:] + t.args)
    if t.disk_gib:
        kwargs["disk"] = EndpointSpec__DiskSpec(type=DiskSpec__DiskType.NETWORK_SSD, size_bytes=t.disk_gib * GIB)
    if t.auth_token_env and env.get(t.auth_token_env):
        kwargs["auth_token"] = env[t.auth_token_env]
    return EndpointSpec(**kwargs)


def job_spec(image: str, module: str, job_args: list[str], env: dict[str, str], platform: str, preset: str, hours: int):
    from nebius.api.nebius.ai.v1 import JobSpec

    return JobSpec(
        image=image,
        container_command="python",
        args=shlex.join(["-m", module, *job_args]),
        environment_variables=_env(APP_ENV, env),
        platform=platform,
        preset=preset,
        restart_attempts=0,
        timeout=timedelta(hours=hours),
    )


async def _by_name(client, project: str, name: str):
    from nebius.aio.service_error import RequestError
    from nebius.api.nebius.ai.v1 import GetEndpointByNameRequest

    try:
        return await client.get_by_name(GetEndpointByNameRequest(parent_id=project, name=name))
    except RequestError:
        return None


async def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="python -m deploy.nebius_deploy")
    ap.add_argument("command", choices=["endpoint", "start", "stop", "status", "job"])
    ap.add_argument("target", nargs="?", help=f"one of {sorted(TARGETS)}, or a jobs.* module for `job`")
    ap.add_argument("--project", default=os.getenv("NEBIUS_PROJECT_ID"))
    ap.add_argument("--image")
    ap.add_argument("--env-file", type=Path, default=Path(".env.nebius"))
    ap.add_argument("--platform")
    ap.add_argument("--preset")
    ap.add_argument("--replace", action="store_true", help="delete an existing endpoint with the same name first")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--hours", type=int, default=6, help="job timeout")
    args, rest = ap.parse_known_args(argv)
    job_args = rest[1:] if rest[:1] == ["--"] else rest
    if not args.project:
        raise SystemExit("--project or NEBIUS_PROJECT_ID is required")

    from nebius.api.nebius.ai.v1 import (
        CreateEndpointRequest,
        CreateJobRequest,
        DeleteEndpointRequest,
        EndpointServiceClient,
        JobServiceClient,
        ListEndpointsRequest,
        StartEndpointRequest,
        StopEndpointRequest,
    )
    from nebius.api.nebius.common.v1 import ResourceMetadata
    from nebius.sdk import SDK

    env = {**load_env_file(args.env_file), **os.environ}
    sdk = SDK()
    try:
        endpoints = EndpointServiceClient(sdk)
        if args.command == "status":
            resp = await endpoints.list(ListEndpointsRequest(parent_id=args.project))
            for e in resp.items:
                print(f"{e.metadata.name:24} {e.status.state.name:12} {list(e.status.public_endpoints)}")
            return
        if args.command == "job":
            if not (args.image and args.target):
                raise SystemExit("job needs a jobs.* module and --image")
            spec = job_spec(args.image, args.target, job_args, env, args.platform or "cpu-e2", args.preset or "8vcpu-32gb", args.hours)
            op = await JobServiceClient(sdk).create(CreateJobRequest(
                metadata=ResourceMetadata(parent_id=args.project, name=f"khatti-{args.target.replace('.', '-')}"),
                spec=spec, dry_run=args.dry_run))
            await op.wait()
            print(f"job {'validated' if args.dry_run else 'created'}: {op.resource_id}")
            return

        t = TARGETS.get(args.target or "")
        if t is None:
            raise SystemExit(f"target must be one of {sorted(TARGETS)}")
        existing = await _by_name(endpoints, args.project, t.name)
        if args.command in ("start", "stop"):
            if existing is None:
                raise SystemExit(f"{t.name} does not exist")
            req = (StartEndpointRequest if args.command == "start" else StopEndpointRequest)(id=existing.metadata.id)
            op = await (endpoints.start(req) if args.command == "start" else endpoints.stop(req))
            await op.wait()
            print(f"{t.name}: {args.command} done")
            return

        if not args.image:
            raise SystemExit("--image is required")
        if existing is not None:
            if not args.replace:
                raise SystemExit(f"{t.name} exists; pass --replace to delete and recreate it")
            if not args.dry_run:
                await (await endpoints.delete(DeleteEndpointRequest(id=existing.metadata.id))).wait()
        spec = endpoint_spec(t, args.image, env, args.platform, args.preset)
        op = await endpoints.create(CreateEndpointRequest(
            metadata=ResourceMetadata(parent_id=args.project, name=t.name), spec=spec, dry_run=args.dry_run))
        await op.wait()
        print(f"{t.name}: {'validated (dry run)' if args.dry_run else 'created ' + op.resource_id}")
    finally:
        await sdk.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
