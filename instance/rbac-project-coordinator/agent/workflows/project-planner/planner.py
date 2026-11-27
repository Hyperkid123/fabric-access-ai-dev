#!/usr/bin/env python3
"""Build and publish the plan requested on a coordinator control ticket."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / ".claude" / "skills"))
sys.path.insert(0, str(Path.cwd() / "presets" / "shared" / "preflight"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "project-coordinator"))
from actions import COMMAND_HELP_FOOTER  # noqa: E402
from jira_mcp import jira_call, jira_cleanup  # noqa: E402
from project_coordinator import (  # noqa: E402
    PLANNER_HEADING,
    PLANNER_RECEIPT,
    comments,
    key_of,
    labels,
    parent_key,
    planner_focus,
    planner_receipt,
    planner_request_path,
    value,
)

COMMENT_LIMIT = 100


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
    if kind in {"inlineCard", "blockCard"}:
        attrs = value.get("attrs")
        url = attrs.get("url") if isinstance(attrs, dict) else None
        return url if isinstance(url, str) else ""
    inner = "".join(plain_text(node) for node in value.get("content") or [])
    return inner + "\n" if kind in {"paragraph", "heading", "codeBlock", "listItem"} else inner


def call(tool: str, args: dict) -> dict:
    result = jira_call(tool, args, timeout=15)
    if not isinstance(result, dict) or result.get("isError") or result.get("success") is False:
        raise RuntimeError(f"{tool} failed for {args['issue_key']}; see the Jira MCP log")
    return result


def get_issue(key: str, *, comments: bool = False, fields: str = "*all") -> dict:
    issue = call("jira_get_issue", {
        "issue_key": key, "fields": fields, "comment_limit": COMMENT_LIMIT if comments else 0,
    })
    if key_of(issue) != key:
        raise RuntimeError(f"Jira response did not contain issue {key}")
    return issue


def issue_context(issue: dict) -> dict:
    return {
        "key": key_of(issue),
        "summary": plain_text(value(issue, "summary")),
        "description": plain_text(value(issue, "description")),
        "repo_labels": sorted({
            label for label in labels(issue)
            if isinstance(label, str) and label.startswith("repo:")
        }),
    }


def load_jira_document(key: str) -> dict:
    issue = get_issue(key, fields="summary,description")
    return {**issue_context(issue), "url": issue.get("url")}


def current_request(control_key: str, comment_id: str) -> dict:
    settings = json.loads(planner_request_path(control_key, comment_id).read_text(encoding="utf-8"))
    control = get_issue(control_key, comments=True)
    parent = parent_key(control)
    if settings["control_label"] not in labels(control) or not parent or settings["parent_key"] != parent:
        raise RuntimeError(f"{control_key}: the control label or parent changed after preflight")
    items = comments(control)
    matched = [item for item in items if str(item.get("id") or "") == comment_id]
    focus = planner_focus(matched[0]) if len(matched) == 1 else None
    if focus is None:
        raise RuntimeError(f"{control_key}: planner command comment was removed or changed")
    if any(planner_receipt(item) == comment_id for item in items):
        raise RuntimeError(f"{control_key}: that planner command already has a published plan")
    parent_context = issue_context(get_issue(parent, fields="summary,description,labels"))
    data = {
        "control_key": control_key,
        "comment_id": comment_id,
        "parent": parent_context,
        "focus": focus,
        "docs_base": settings["docs_base"],
        "docs_glob": settings["docs_glob"],
        "warnings": [] if parent_context["description"].strip() else [f"Parent ticket {parent} has an empty description."],
    }
    request_id = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return {**data, "request_id": request_id}


def publish(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plan = manifest_path.with_name("plan.md").read_text(encoding="utf-8").strip()
    if not plan or len(plan) > 24_000:
        raise ValueError("plan.md must contain between 1 and 24,000 characters")
    control_key, comment_id = manifest["control_key"], manifest["comment_id"]
    request = current_request(control_key, comment_id)
    if manifest["request_id"] != request["request_id"]:
        raise RuntimeError(f"{control_key}: the planner inputs changed after the plan was drafted")
    warnings = manifest["warnings"]
    parent = request["parent"]["key"]
    body = f"### {PLANNER_HEADING} for {parent}\n\n"
    if warnings:
        body += "**Context gaps**\n\n" + "".join(f"- {warning}\n" for warning in warnings) + "\n"
    body += f"{plan}\n\n{COMMAND_HELP_FOOTER}\n\n{PLANNER_RECEIPT} {comment_id}"
    comment = call("jira_add_comment", {"issue_key": control_key, "comment": body})
    return {"status": "published", "control_key": control_key, "comment_id": comment.get("id"), "warnings": warnings}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    build = commands.add_parser("build")
    build.add_argument("control_key")
    build.add_argument("comment_id")
    post = commands.add_parser("publish")
    post.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "build":
            from build_planner_context import build_context

            result = build_context(
                current_request(args.control_key, args.comment_id), load_jira_document=load_jira_document,
            )
        else:
            result = publish(args.manifest)
        print(json.dumps(result))
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        parser.exit(1, f"Planner: {exc}\n")
    finally:
        jira_cleanup()


if __name__ == "__main__":
    main()
