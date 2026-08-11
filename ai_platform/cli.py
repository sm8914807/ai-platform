"""CLI entry point."""

import argparse
import asyncio
import json
from pathlib import Path

from ai_platform.sdk.platform import Platform


def main() -> None:
    parser = argparse.ArgumentParser(prog="platform", description="AI Platform CLI")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run an agent or workflow")
    run_p.add_argument("ref", help="Resource ref e.g. agents/support-agent")
    run_p.add_argument("--input", default="{}", help="JSON input")
    run_p.add_argument("--namespace", default="default-org/default-project")
    run_p.add_argument("--env", default="development")
    run_p.add_argument("--endpoint", default="http://localhost:8080")
    run_p.add_argument("--multi-agent", action="store_true", help="Force multi-agent mode")

    login_p = sub.add_parser("login", help="SSO login (dev)")
    login_p.add_argument("--email", required=True)
    login_p.add_argument("--org", default="default-org")
    login_p.add_argument("--endpoint", default="http://localhost:8080")

    apply_p = sub.add_parser("apply", help="Git-sync apply YAML resources")
    apply_p.add_argument("-f", "--directory", required=True, help="Resources directory")
    apply_p.add_argument("--namespace", default="default-org/default-project")
    apply_p.add_argument("--env", default="development")
    apply_p.add_argument("--endpoint", default="http://localhost:8080")

    tf_p = sub.add_parser("tf-export", help="Export Terraform files")
    tf_p.add_argument("--namespace", default="default-org/default-project")
    tf_p.add_argument("--output", default="./terraform")
    tf_p.add_argument("--endpoint", default="http://localhost:8080")

    edge_p = sub.add_parser("edge", help="Start edge runtime (cached bundle)")
    edge_p.add_argument("--namespace", default="default-org/default-project")
    edge_p.add_argument("--env", default="development")
    edge_p.add_argument("--endpoint", default="http://localhost:8080")
    edge_p.add_argument("--region", default=None)
    edge_p.add_argument("--cache", default=".platform/edge-bundle.json")
    edge_p.add_argument("--telemetry-only", action="store_true")

    compliance_p = sub.add_parser("compliance-install", help="Install compliance pack")
    compliance_p.add_argument("pack_id")
    compliance_p.add_argument("--namespace", default="default-org/default-project")
    compliance_p.add_argument("--endpoint", default="http://localhost:8080")

    args = parser.parse_args()
    if args.command == "compliance-install":
        import httpx

        async def _install():
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{args.endpoint}/v1/{args.namespace}/compliance/install",
                    json={"packId": args.pack_id},
                )
                print(json.dumps(r.json(), indent=2))

        asyncio.run(_install())
        return

    if args.command == "edge":
        async def _edge():
            from ai_platform import EdgeRuntime

            runtime = await EdgeRuntime.start(
                endpoint=args.endpoint,
                namespace=args.namespace,
                environment=args.env,
                region=args.region,
                telemetry_only=args.telemetry_only,
                cache_path=args.cache,
            )
            print(json.dumps({"status": "edge_runtime_ready", "cache": args.cache}))

        asyncio.run(_edge())
        return

    if args.command == "version":
        from ai_platform import __version__

        print(__version__)
        return

    if args.command == "login":
        import httpx

        async def _login():
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{args.endpoint}/v1/auth/login",
                    json={"email": args.email, "orgId": args.org},
                )
                print(json.dumps(r.json(), indent=2))

        asyncio.run(_login())
        return

    if args.command == "apply":
        import httpx

        async def _apply():
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{args.endpoint}/v1/{args.namespace}/git-sync",
                    json={"directory": args.directory, "publish": True},
                )
                print(json.dumps(r.json(), indent=2))

        asyncio.run(_apply())
        return

    if args.command == "tf-export":
        import httpx

        async def _export():
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    f"{args.endpoint}/v1/{args.namespace}/terraform/export",
                    params={"directory": args.output},
                )
                print(json.dumps(r.json(), indent=2))

        asyncio.run(_export())
        return

    if args.command == "run":
        async def _run():
            p = await Platform.start(
                namespace=args.namespace,
                environment=args.env,
                endpoint=args.endpoint,
            )
            result = await p.run(args.ref, input=json.loads(args.input), stream=True)
            async for ev in result.stream:
                print(json.dumps({"type": ev.type, "data": ev.data}))

        asyncio.run(_run())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
