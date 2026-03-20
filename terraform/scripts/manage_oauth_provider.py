#!/usr/bin/env python3
import argparse
import json
import os
import sys

import boto3
from botocore.exceptions import ClientError


def session(profile: str | None, region: str):
    kwargs = {"region_name": region}
    if profile:
        kwargs["profile_name"] = profile
    return boto3.Session(**kwargs)


def client(profile: str | None, region: str):
    return session(profile, region).client("bedrock-agentcore-control")


def provider_input(args: argparse.Namespace) -> dict:
    client_secret = os.getenv("AGENTCORE_OAUTH_CLIENT_SECRET", "")
    if not client_secret:
        raise SystemExit("AGENTCORE_OAUTH_CLIENT_SECRET is required for upsert.")

    return {
        "credentialProviderVendor": args.vendor,
        "name": args.name,
        "oauth2ProviderConfigInput": {
            "includedOauth2ProviderConfig": {
                "clientId": args.client_id,
                "clientSecret": client_secret,
                "authorizationEndpoint": args.authorization_endpoint,
                "tokenEndpoint": args.token_endpoint,
                "issuer": args.issuer,
            }
        },
    }


def get_provider(c, name: str) -> dict | None:
    try:
        return c.get_oauth2_credential_provider(name=name)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return None
        raise


def cmd_upsert(args: argparse.Namespace) -> int:
    c = client(args.profile, args.region)
    payload = provider_input(args)
    existing = get_provider(c, args.name)

    if existing is None:
        result = c.create_oauth2_credential_provider(**payload)
    else:
        c.update_oauth2_credential_provider(**payload)
        result = c.get_oauth2_credential_provider(name=args.name)

    print(
        json.dumps(
            {
                "name": result["name"],
                "credentialProviderArn": result["credentialProviderArn"],
                "callbackUrl": result.get("callbackUrl", ""),
            }
        )
    )
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    c = client(args.profile, args.region)
    result = get_provider(c, args.name)
    if result is None:
        raise SystemExit(f"OAuth provider '{args.name}' was not found.")

    print(
        json.dumps(
            {
                "name": result["name"],
                "credential_provider_arn": result["credentialProviderArn"],
                "secret_arn": result["clientSecretArn"]["secretArn"],
                "callback_url": result.get("callbackUrl", ""),
            }
        )
    )
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    c = client(args.profile, args.region)
    existing = get_provider(c, args.name)
    if existing is None:
        return 0

    c.delete_oauth2_credential_provider(name=args.name)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--region", required=True)
    common.add_argument("--name", required=True)

    get_parser = subparsers.add_parser("get", parents=[common])
    get_parser.set_defaults(func=cmd_get)

    delete_parser = subparsers.add_parser("delete", parents=[common])
    delete_parser.set_defaults(func=cmd_delete)

    upsert_parser = subparsers.add_parser("upsert", parents=[common])
    upsert_parser.add_argument("--vendor", default="CognitoOauth2")
    upsert_parser.add_argument("--client-id", required=True)
    upsert_parser.add_argument("--authorization-endpoint", required=True)
    upsert_parser.add_argument("--token-endpoint", required=True)
    upsert_parser.add_argument("--issuer", required=True)
    upsert_parser.set_defaults(func=cmd_upsert)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
