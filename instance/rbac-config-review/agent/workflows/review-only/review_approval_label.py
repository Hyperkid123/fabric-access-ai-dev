#!/usr/bin/env python3
"""Clear or synchronize the verified-review label for a pinned PR revision."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import quote


APPROVAL_LABEL = {
    "name": "dev-bot-review-approved",
    "color": "0e8a16",
    "description": "Review bot verified no outstanding findings for the current revision.",
}
SHA = re.compile(r"^[0-9a-f]{40}$")


def _status(result: subprocess.CompletedProcess) -> int | None:
    response = result.stdout + "\n" + result.stderr
    match = re.search(r"(?m)^HTTP/\S+\s+(\d{3})\b", response)
    return int(match.group(1)) if match else None


def _api(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", "api", *args], capture_output=True, text=True,
                          timeout=30, check=False)


def _require_success(result: subprocess.CompletedProcess, message: str) -> None:
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "GitHub returned no response"
        raise RuntimeError(f"{message}: {detail[:300]}")


def _validate(repo: str, pr: int, head: str, base: str) -> None:
    if (not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
            or pr < 1 or not SHA.fullmatch(head) or not SHA.fullmatch(base)):
        raise ValueError("Repository, PR, or revision is invalid")


def _check_revision(repo: str, pr: int, head: str, base: str) -> None:
    result = _api([f"repos/{repo}/pulls/{pr}"])
    _require_success(result, "Cannot read PR before approval labeling")
    pull = json.loads(result.stdout)
    if (not isinstance(pull, dict) or pull.get("state") != "open"
            or not isinstance(pull.get("head"), dict)
            or not isinstance(pull.get("base"), dict)
            or pull["head"].get("sha") != head
            or pull["base"].get("sha") != base):
        raise RuntimeError("PR head or base changed before approval labeling")


def _label_path(repo: str) -> str:
    return f"repos/{repo}/labels/{quote(APPROVAL_LABEL['name'], safe='')}"


def _ensure_label(repo: str) -> None:
    path = _label_path(repo)
    lookup = _api(["--include", path])
    status = _status(lookup)
    if status == 404:
        created = _api([
            f"repos/{repo}/labels", "--method", "POST",
            "--field", f"name={APPROVAL_LABEL['name']}",
            "--field", f"color={APPROVAL_LABEL['color']}",
            "--field", f"description={APPROVAL_LABEL['description']}",
        ])
        if created.returncode == 0:
            return
        lookup = _api(["--include", path])
        status = _status(lookup)
    if lookup.returncode or status != 200:
        detail = lookup.stderr.strip() or lookup.stdout.strip() or "GitHub returned no response"
        raise RuntimeError(f"Cannot create or check approval label: {detail[:300]}")
    updated = _api([
        path, "--method", "PATCH",
        "--field", f"color={APPROVAL_LABEL['color']}",
        "--field", f"description={APPROVAL_LABEL['description']}",
    ])
    _require_success(updated, "Cannot update approval label")


def _remove_label(repo: str, pr: int) -> bool:
    result = _api([
        "--include",
        f"repos/{repo}/issues/{pr}/labels/{quote(APPROVAL_LABEL['name'], safe='')}",
        "--method", "DELETE",
    ])
    if result.returncode and _status(result) == 404:
        return False
    _require_success(result, "Cannot remove stale approval label")
    return _status(result) != 404


def _add_label(repo: str, pr: int) -> None:
    result = _api([
        f"repos/{repo}/issues/{pr}/labels", "--method", "POST",
        "--field", f"labels[]={APPROVAL_LABEL['name']}",
    ])
    _require_success(result, "Cannot add approval label")


def synchronize(repo: str, pr: int, head: str, base: str, mode: str,
                ledger_path: Path | None = None) -> dict:
    _validate(repo, pr, head, base)
    if mode not in {"clear", "sync"}:
        raise ValueError("Mode must be clear or sync")
    if mode == "sync" and ledger_path is None:
        raise ValueError("sync mode requires the verified ledger")
    if mode == "clear" and ledger_path is not None:
        raise ValueError("clear mode does not accept a ledger")

    _check_revision(repo, pr, head, base)
    if mode == "clear":
        removed = _remove_label(repo, pr)
        return {"label": APPROVAL_LABEL["name"], "action": "removed" if removed else "absent"}

    ledger = json.loads(ledger_path.read_text())
    if not isinstance(ledger, list) or any(
        not isinstance(entry, dict) or entry.get("status") not in {"open", "addressed"}
        for entry in ledger
    ):
        raise ValueError("Verified ledger must contain only open or addressed findings")
    approved = all(entry["status"] == "addressed" for entry in ledger)
    if not approved:
        removed = _remove_label(repo, pr)
        _check_revision(repo, pr, head, base)
        return {
            "label": APPROVAL_LABEL["name"],
            "action": "removed" if removed else "absent",
            "open_findings": sum(entry["status"] == "open" for entry in ledger),
        }

    _ensure_label(repo)
    _add_label(repo, pr)
    try:
        _check_revision(repo, pr, head, base)
    except Exception:
        _remove_label(repo, pr)
        raise
    return {
        "label": APPROVAL_LABEL["name"],
        "action": "added",
        "verified_findings": len(ledger),
        "head_sha": head,
        "base_sha": base,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--mode", choices=("clear", "sync"), required=True)
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(synchronize(args.repo, args.pr, args.head_sha, args.base_sha,
                                     args.mode, args.ledger)))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Review approval label update failed: {exc}\n")


if __name__ == "__main__":
    main()
