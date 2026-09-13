"""publisherctl — offline operator surface for the W5 publishers package.

Zero network by construction: every command renders gates and plans only.
``dry-run`` exercises the exact same plan builder the live path uses, so the
rendered receipt is the deploy contract, not a mock.

Commands:
  gate-report  [--json] [--approvals-dir DIR]
  dry-run      --channel C --job-id J --title T [--caption C] --video PATH
               [--hashtags a,b] [--visibility public|unlisted|private]
               [--json] [--approvals-dir DIR]
  adapters     [--json]

Exit codes: 0 ok · 1 refused/error · 2 usage (argparse).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from base import CHANNELS, PublishRequest, PublisherError, adapter_registry, evaluate_gate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="publisherctl", description="W5 publishers — dark by default")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("gate-report", help="show the dark gate for every channel")
    report.add_argument("--json", action="store_true")
    report.add_argument("--approvals-dir", default=None)

    dry = sub.add_parser("dry-run", help="render the exact publish plan without network")
    dry.add_argument("--channel", required=True, choices=sorted(CHANNELS))
    dry.add_argument("--job-id", required=True)
    dry.add_argument("--title", required=True)
    dry.add_argument("--caption", default="")
    dry.add_argument("--video", required=True)
    dry.add_argument("--hashtags", default="")
    dry.add_argument("--visibility", default="public", choices=["public", "unlisted", "private"])
    dry.add_argument("--json", action="store_true")
    dry.add_argument("--approvals-dir", default=None)

    listing = sub.add_parser("adapters", help="list adapters and credential env names")
    listing.add_argument("--json", action="store_true")
    return parser


def cmd_gate_report(args: Any, out: Any) -> int:
    decisions = {
        channel: evaluate_gate(channel, approvals_dir=args.approvals_dir).to_dict() for channel in CHANNELS
    }
    if args.json:
        print(
            json.dumps({"env_unlock_var": "PUBLISH_LIVE", "decisions": decisions}, indent=2, sort_keys=True),
            file=out,
        )
        return 0
    print("channel    env_ok  approval_ok  live_allowed  reason", file=out)
    for channel, decision in decisions.items():
        print(
            "{:<10} {:<6} {:<11} {:<12} {}".format(
                channel,
                str(decision["env_ok"]),
                str(decision["approval_ok"]),
                str(decision["live_allowed"]),
                decision["reason"] or "gate open",
            ),
            file=out,
        )
    allowed = sum(1 for decision in decisions.values() if decision["live_allowed"])
    print("live_allowed: {}/{}".format(allowed, len(decisions)), file=out)
    return 0


def cmd_dry_run(args: Any, out: Any) -> int:
    adapter = adapter_registry()[args.channel](approvals_dir=args.approvals_dir)
    hashtags = tuple(tag.strip() for tag in str(args.hashtags).split(",") if tag.strip())
    request = PublishRequest(
        channel=args.channel,
        job_id=args.job_id,
        title=args.title,
        caption=args.caption,
        video_path=args.video,
        hashtags=hashtags,
        visibility=args.visibility,
    )
    receipt = adapter.publish(request)
    print(json.dumps(receipt.to_dict(), indent=2, sort_keys=True, default=str), file=out)
    return 0


def cmd_adapters(args: Any, out: Any) -> int:
    registry = adapter_registry()
    payload = {
        channel: {
            "adapter": cls.__name__,
            "dry_run_default": True,
            "credential_env": sorted(cls.credential_spec),
        }
        for channel, cls in registry.items()
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True), file=out)
        return 0
    for channel, info in payload.items():
        print("{:<10} {} creds={}".format(channel, info["adapter"], ",".join(info["credential_env"])), file=out)
    return 0


def main(argv: Any = None, *, stdout: Any = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    try:
        if args.command == "gate-report":
            return cmd_gate_report(args, out)
        if args.command == "dry-run":
            return cmd_dry_run(args, out)
        if args.command == "adapters":
            return cmd_adapters(args, out)
    except PublisherError as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
