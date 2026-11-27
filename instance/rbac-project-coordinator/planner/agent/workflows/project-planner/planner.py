#!/usr/bin/env python3
"""Discover, snapshot, and publish Jira planning requests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / ".claude" / "skills"))
from jira_mcp import jira_call, jira_cleanup  # noqa: E402

LABEL = "dev-bot-planner"
KEY = re.compile(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*")
UNASSIGNED = {"", "unassigned", "unassigned user"}
MARKER = "Planner request ID:"
REQUEST_DIRECTORY = Path("data/planner-requests")
REQUEST_ID = re.compile(r"[0-9a-f]{64}")
COMMENT_LIMIT = 100


def field(issue: dict, name: str):
    fields = issue.get("fields")
    return fields.get(name, issue.get(name)) if isinstance(fields, dict) else issue.get(name)


def issue_key(issue: dict | str | None) -> str:
    if isinstance(issue, dict):
        issue = issue.get("key") or issue.get("issue_key")
    return issue if isinstance(issue, str) and KEY.fullmatch(issue) else ""


def plain_text(value: object) -> str:
    """Read Jira Server text and Jira Cloud's Atlassian Document Format."""
    if isinstance(value, str):
        if value.lstrip().startswith("{"):
            try:
                parsed = json.loads(value)
            except ValueError:
                return value
            if isinstance(parsed, dict) and parsed.get("type") == "doc":
                return plain_text(parsed)
        return value
    if not isinstance(value, dict):
        return ""
    kind = value.get("type")
    if kind == "text":
        return value.get("text") or ""
    if kind == "hardBreak":
        return "\n"
    inner = "".join(plain_text(node) for node in value.get("content") or [])
    return inner + "\n" if kind in {"paragraph", "heading", "codeBlock", "listItem"} else inner


def unassigned(value: object) -> bool:
    if value is None or value == {}:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in UNASSIGNED
    if not isinstance(value, dict):
        return False
    identifiers = [value.get(name) for name in (
        "accountId", "account_id", "name", "key", "id", "username", "emailAddress", "email",
    )]
    if any(ident is not None and str(ident).strip().casefold() not in UNASSIGNED for ident in identifiers):
        return False
    display_name = value.get("displayName") or value.get("display_name")
    return isinstance(display_name, str) and display_name.strip().casefold() in UNASSIGNED


@dataclass(frozen=True)
class Settings:
    project: str
    docs_base: str
    docs_glob: str

    def __post_init__(self):
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.project):
            raise ValueError("BOT_JIRA_PROJECT must be a Jira project key")

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            os.getenv("BOT_JIRA_PROJECT", "RHCLOUD").strip().upper(),
            os.getenv("BOT_PLANNER_DOCS_BASE_URL", "").strip(),
            os.getenv("BOT_PLANNER_DOCS_GLOB", "").strip() or "*.md",
        )


def request_file(key: str, request_id: str) -> Path:
    if not KEY.fullmatch(key) or not REQUEST_ID.fullmatch(request_id):
        raise ValueError("Invalid Jira key or planner request ID")
    return REQUEST_DIRECTORY / f"{key}-{request_id}.json"


def call(tool: str, args: dict) -> dict:
    result = jira_call(tool, args, timeout=15)
    if not isinstance(result, dict) or result.get("isError") or result.get("success") is False:
        raise RuntimeError(f"{tool} failed for {args.get('issue_key', 'the planner queue')}; see the Jira MCP log")
    return result


def get_issue(key: str, *, comments: bool = False) -> dict:
    issue = call("jira_get_issue", {
        "issue_key": key, "fields": "*all", "comment_limit": COMMENT_LIMIT if comments else 0,
    })
    if issue_key(issue) != key:
        raise RuntimeError(f"Jira response did not contain issue {key}")
    return issue


def queue(settings: Settings):
    args = {
        "jql": f'project = "{settings.project}" AND labels = "{LABEL}" AND assignee IS EMPTY ORDER BY key ASC',
        "fields": "key", "limit": 50,
    }
    offset = 0
    seen_tokens = set()
    while True:
        result = call("jira_search", args)
        issues = result.get("issues")
        if not isinstance(issues, list):
            raise RuntimeError("Jira search did not return an issue list")
        for item in issues:
            if key := issue_key(item):
                yield key
        offset += len(issues)
        token = result.get("next_page_token") or result.get("nextPageToken")
        if token:
            if token in seen_tokens:
                raise RuntimeError("Jira search repeated a page token")
            seen_tokens.add(token)
            args.pop("start_at", None)
            args["page_token"] = token
        elif not seen_tokens and issues and isinstance(result.get("total"), int) and offset < result["total"]:
            args["start_at"] = offset
        else:
            return


def epic_for(issue: dict, cache: dict[str, dict]) -> dict | None:
    current = issue
    visited = {issue_key(issue)}
    for _ in range(5):
        kind = field(current, "issuetype") or field(current, "issue_type")
        if isinstance(kind, dict):
            kind = kind.get("name")
        if isinstance(kind, str) and kind.casefold() == "epic":
            return current
        parent = issue_key(field(current, "parent")) or issue_key(field(current, "epic_key"))
        if not parent or parent in visited:
            return None
        visited.add(parent)
        if parent not in cache:
            cache[parent] = get_issue(parent)
        current = cache[parent]
    return None


def issue_context(issue: dict) -> dict:
    return {
        "key": issue_key(issue),
        "summary": plain_text(field(issue, "summary")),
        "description": plain_text(field(issue, "description")),
        "repo_labels": sorted({
            label for label in field(issue, "labels") or []
            if isinstance(label, str) and label.startswith("repo:")
        }),
    }


def snapshot(issue: dict, settings: Settings, cache: dict[str, dict]) -> dict | None:
    key = issue_key(issue)
    if (not key.startswith(settings.project + "-") or LABEL not in (field(issue, "labels") or [])
            or not unassigned(field(issue, "assignee"))):
        return None
    warnings = []
    try:
        epic = epic_for(issue, cache)
    except RuntimeError:
        epic = None
        warnings.append("Jira could not read the Epic or one of its parents.")
    if epic is None and not warnings:
        warnings.append("No Epic was found. Context comes from the planning ticket.")
    data = {
        "issue": issue_context(issue),
        "epic": issue_context(epic) if epic is not None else None,
        "docs_base": settings.docs_base,
        "docs_glob": settings.docs_glob,
        "warnings": warnings,
    }
    request_id = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return {**data, "request_id": request_id}


def published(issue: dict, request_id: str) -> bool:
    items = field(issue, "comments")
    if not isinstance(items, list):
        comment_field = field(issue, "comment") or {}
        items = comment_field.get("comments", []) if isinstance(comment_field, dict) else []
    for comment in items:
        if isinstance(comment, dict):
            lines = plain_text(comment.get("body")).rstrip().splitlines()
            if lines and lines[-1].strip() == f"{MARKER} {request_id}":
                return True
    if len(items) >= COMMENT_LIMIT:
        # The Jira MCP issue tool caps comments and returns the oldest first.
        key = issue_key(issue)
        result = call("jira_search", {
            "jql": f'key = "{key}" AND comment ~ "{request_id}"', "fields": "key", "limit": 1,
        })
        if not isinstance(result.get("issues"), list):
            raise RuntimeError(f"Jira could not check for a prior plan on {key}")
        return any(issue_key(item) == key for item in result["issues"])
    return False


def preflight() -> None:
    from common import output_result

    try:
        settings = Settings.from_environment()
        cache: dict[str, dict] = {}
        for key in queue(settings):
            issue = get_issue(key, comments=True)
            request = snapshot(issue, settings, cache)
            if request and not published(issue, request["request_id"]):
                request_id = request["request_id"]
                # OpenCode receives a sanitized environment. Pass only this
                # cycle's planner settings through the shared local workspace.
                handoff = request_file(key, request_id)
                handoff.parent.mkdir(parents=True, exist_ok=True)
                handoff.write_text(json.dumps(asdict(settings)), encoding="utf-8")
                output_result("start", json.dumps({"issue_key": key, "request_id": request_id}))
                return
        output_result("skip", f"No new unassigned Jira requests labeled {LABEL}")
    except (RuntimeError, ValueError) as exc:
        output_result("error", str(exc))
    finally:
        jira_cleanup()


def current_request(key: str, request_id: str) -> dict:
    settings = Settings(**json.loads(request_file(key, request_id).read_text(encoding="utf-8")))
    issue = get_issue(key, comments=True)
    request = snapshot(issue, settings, {})
    if not request or request["request_id"] != request_id:
        raise RuntimeError(f"{key}: the planning request changed or is no longer eligible")
    if published(issue, request_id):
        raise RuntimeError(f"{key}: this planning request already has a published plan")
    return request


def publish(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = manifest_path.with_name("plan.md").read_text(encoding="utf-8").strip()
    if not plan or len(plan) > 24_000:
        raise ValueError("plan.md must contain between 1 and 24,000 characters")
    key = manifest["issue"]["key"]
    request = current_request(key, manifest["request_id"])
    warnings = manifest["warnings"]
    body = f"## Planner update — Technical plan for {key}\n\n"
    if warnings:
        body += "**Context gaps**\n\n" + "".join(f"- {warning}\n" for warning in warnings) + "\n"
    body += f"{plan}\n\n{MARKER} {request['request_id']}"
    comment = call("jira_add_comment", {"issue_key": key, "comment": body})
    return {"status": "published", "issue_key": key, "comment_id": comment.get("id"), "warnings": warnings}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    build = commands.add_parser("build")
    build.add_argument("issue_key")
    build.add_argument("request_id")
    post = commands.add_parser("publish")
    post.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "build":
            from build_planner_context import build_context

            result = build_context(current_request(args.issue_key, args.request_id))
        else:
            result = publish(args.manifest)
        print(json.dumps(result))
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        parser.exit(1, f"Planner: {exc}\n")
    finally:
        jira_cleanup()


if __name__ == "__main__":
    main()
