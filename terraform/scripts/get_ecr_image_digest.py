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
    return session(profile, region).client("ecr")


def cmd_get(args: argparse.Namespace) -> int:
    try:
        response = client(args.profile, args.region).describe_images(
            repositoryName=args.repository_name,
            imageIds=[{"imageTag": args.image_tag}],
        )
    except ClientError as exc:
        raise SystemExit(str(exc))

    image_details = response.get("imageDetails", [])
    if not image_details:
        raise SystemExit(
            f"ECR image '{args.repository_name}:{args.image_tag}' was not found."
        )

    image_digest = image_details[0].get("imageDigest")
    if not image_digest:
        raise SystemExit(
            f"ECR image '{args.repository_name}:{args.image_tag}' did not include an imageDigest."
        )

    print(json.dumps({"image_digest": image_digest}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=os.getenv("AWS_PROFILE"))
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--region", required=True)
    common.add_argument("--repository-name", required=True)
    common.add_argument("--image-tag", required=True)

    get_parser = subparsers.add_parser("get", parents=[common])
    get_parser.set_defaults(func=cmd_get)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
