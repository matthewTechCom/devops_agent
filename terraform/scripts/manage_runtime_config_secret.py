#!/usr/bin/env python3
import argparse
import json
import os
import sys

import boto3


def session(profile: str | None, region: str):
    kwargs = {"region_name": region}
    if profile:
        kwargs["profile_name"] = profile
    return boto3.Session(**kwargs)


def client(profile: str | None, region: str):
    return session(profile, region).client("secretsmanager")


def cmd_upsert(args: argparse.Namespace) -> int:
    raw_payload = os.getenv("RUNTIME_CONFIG_SECRET_STRING", "")
    if not raw_payload:
        raise SystemExit("RUNTIME_CONFIG_SECRET_STRING is required for upsert.")

    payload = json.loads(raw_payload)
    if not isinstance(payload, dict):
        raise SystemExit("RUNTIME_CONFIG_SECRET_STRING must be a JSON object.")

    client(args.profile, args.region).put_secret_value(
        SecretId=args.secret_id,
        SecretString=json.dumps(payload, separators=(",", ":"), sort_keys=True),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--region", required=True)
    common.add_argument("--secret-id", required=True)

    upsert_parser = subparsers.add_parser("upsert", parents=[common])
    upsert_parser.set_defaults(func=cmd_upsert)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
