#!/usr/bin/env python3
"""Deduplicate and rank the dimension results for a pinned review plan."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SEVERITY = {name: rank for rank, name in enumerate(("info", "low", "medium", "high", "critical"))}
MIN_CONFIDENCE = 0.7


def title_words(title: str) -> set[str]:
    return set(re.sub(r"`[^`]*`", " ", title.casefold()).split())


def nearby(left: dict, right: dict, line_range: int) -> bool:
    left_line, right_line = left.get("line"), right.get("line")
    return (bool(left.get("file")) and left["file"] == right.get("file")
            and type(left_line) is int and left_line > 0
            and type(right_line) is int and right_line > 0
            and abs(left_line - right_line) <= line_range)


def duplicate(left: dict, right: dict, jaccard: float, line_range: int) -> bool:
    if left["dimension"] == right["dimension"] or "info" in (left["severity"], right["severity"]):
        return False
    if nearby(left, right, line_range):
        return True
    a, b = title_words(left["finding"]), title_words(right["finding"])
    return bool(a or b) and len(a & b) / len(a | b) >= jaccard


def valid_finding(finding: object) -> bool:
    if not isinstance(finding, dict):
        return False
    confidence, title = finding.get("confidence"), finding.get("finding")
    return (isinstance(title, str) and bool(title.strip())
            and isinstance(finding.get("severity"), str) and finding["severity"] in SEVERITY
            and type(confidence) in (int, float) and 0 <= confidence <= 1)


def valid_result(result: object, name: str) -> bool:
    return (isinstance(result, dict) and result.get("dimension") == name
            and result.get("verdict") in ("pass", "warn", "fail")
            and isinstance(result.get("severity"), str) and result["severity"] in SEVERITY
            and isinstance(result.get("summary"), str) and bool(result["summary"].strip())
            and isinstance(result.get("findings"), list) and all(map(valid_finding, result["findings"])))


def merge_results(plan: dict, results: dict) -> dict:
    if not isinstance(results, dict):
        raise ValueError("Dimension results must be an object keyed by planned dimension")
    passes = plan["passes"]
    unexpected = results.keys() - {item["name"] for item in passes}
    if unexpected:
        raise ValueError(f"Results contain unplanned dimensions: {', '.join(sorted(unexpected))}")

    rules = plan["merge"]
    groups: list[list[dict]] = []
    completed, failed = [], []
    for review_pass in passes:
        name = review_pass["name"]
        result = results.get(name)
        if not valid_result(result, name):
            failed.append(name)
            continue
        completed.append(name)
        for finding in result["findings"]:
            if finding["confidence"] < MIN_CONFIDENCE:
                continue
            item = {**finding, "dimension": name, "source_skill": review_pass.get("source_skill"),
                    "source_repo": review_pass.get("source_repo")}
            # Every pair must match. This keeps a nearby third finding from
            # bridging distinct issues or joining two findings from one pass.
            for group in groups:
                if all(duplicate(item, member, rules["dedup_jaccard"], rules["dedup_line_range"])
                       for member in group):
                    group.append(item)
                    break
            else:
                groups.append([item])

    findings = []
    for group in groups:
        canonical = max(group, key=lambda item: len(str(item.get("evidence") or "")))
        severity = max((item["severity"] for item in group), key=SEVERITY.__getitem__)
        count = len(group)
        if count >= 5 and severity == "low":
            severity = "medium"
        findings.append({**canonical, "severity": severity,
                         "agreed_by": [item["dimension"] for item in group],
                         "agreed_by_skills": list(dict.fromkeys(item["source_skill"] for item in group
                                                               if item["source_skill"])),
                         "agreed_count": count, "consensus": count >= rules["consensus_threshold"]})
    findings.sort(key=lambda item: (item["agreed_count"], SEVERITY[item["severity"]], item["confidence"]),
                  reverse=True)
    return {"completed_dimensions": completed, "failed_dimensions": failed, "findings": findings}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        merged = merge_results(json.loads(args.plan.read_text()), json.loads(args.results.read_text()))
        args.output.write_text(json.dumps(merged, indent=2) + "\n")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"Cannot merge review results: {exc}\n")


if __name__ == "__main__":
    main()
