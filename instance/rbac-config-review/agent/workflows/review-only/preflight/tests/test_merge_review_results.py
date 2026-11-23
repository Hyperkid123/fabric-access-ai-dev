"""Preserve distinct findings and their attribution during deterministic merge."""

import json
import sys
from pathlib import Path

WORKFLOW_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKFLOW_DIR))

import merge_review_results as merge  # noqa: E402


def plan(*names):
    return {
        "passes": [{"name": name, "source_skill": f"review:{name}", "source_repo": "catalog"}
                   for name in names],
        "merge": {"dedup_jaccard": 0.4, "dedup_line_range": 5, "consensus_threshold": 3},
    }


def result(name, *findings):
    return {"dimension": name, "verdict": "warn", "severity": "low",
            "summary": "Completed", "findings": list(findings)}


def finding(title, file, line, **fields):
    return {"finding": title, "file": file, "line": line, "severity": "low", "confidence": 0.9,
            **fields}


def test_merge_cli_uses_consensus_and_keeps_the_highest_severity(tmp_path, monkeypatch):
    plan_path, results_path, output = [tmp_path / name for name in ("plan.json", "raw.json", "merged.json")]
    plan_path.write_text(json.dumps(plan("security", "correctness", "integration", "performance")))
    results_path.write_text(json.dumps({
        "integration": result("integration", finding("Missing authorization check", "view.py", 8)),
        "correctness": result("correctness", finding("Guard absent", "view.py", 4,
                                                     evidence="Longer evidence")),
        "security": result("security", finding("Missing authorization check", "view.py", 2,
                                               severity="high", evidence="Short")),
        "performance": result("performance", finding("Missing authorization check", "view.py", 3,
                                                     confidence=0.4)),
    }))
    monkeypatch.setattr(sys, "argv", ["merge_review_results.py", "--plan", str(plan_path),
                                     "--results", str(results_path), "--output", str(output)])
    merge.main()
    merged = json.loads(output.read_text())
    assert merged["completed_dimensions"] == ["security", "correctness", "integration", "performance"]
    assert merged["failed_dimensions"] == []
    item, = merged["findings"]
    assert item["dimension"] == "correctness"
    assert item["agreed_by"] == ["security", "correctness", "integration"]
    assert item["agreed_by_skills"] == ["review:security", "review:correctness", "review:integration"]
    assert item["agreed_count"] == 3 and item["consensus"] is True
    assert item["severity"] == "high"


def test_merge_keeps_distinct_findings_and_reports_incomplete_passes():
    merged = merge.merge_results(plan("correctness", "security", "concurrency", "db-schema", "integration"), {
        "correctness": result("correctness", finding("Incorrect default", "view.py", 2),
                              finding("Expired cache", "view.py", 1, severity="high")),
        "security": result("security", finding("Missing tenant guard", "view.py", 4)),
        "concurrency": result("concurrency", finding("Async race", "view.py", 8)),
        "db-schema": {"findings": []},
    })
    assert merged["failed_dimensions"] == ["db-schema", "integration"]
    assert len(merged["findings"]) == 3
    assert [item["agreed_by"] for item in merged["findings"]] == [
        ["correctness", "security"], ["correctness"], ["concurrency"],
    ]
