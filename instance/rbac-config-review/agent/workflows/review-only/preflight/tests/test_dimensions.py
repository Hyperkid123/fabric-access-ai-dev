"""The default review includes the operational and documentation passes."""

from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parents[4] / "skills" / "dimensional-review"


def test_documentation_and_observability_are_selected_for_any_changed_file():
    catalog = yaml.safe_load((SKILL / "dimensions.yaml").read_text())
    assert catalog["review_levels"]["default"] == "high"
    assert catalog["review_levels"]["presets"]["high"]["all_applicable"] is True
    by_name = {entry["name"]: entry for entry in catalog["dimensions"]}
    for name in ("documentation", "observability"):
        dimension = by_name[name]
        assert dimension["languages"] == "all"
        assert "file_patterns" not in dimension
        assert (SKILL / dimension["source"]["path"]).is_file()
        assert name in catalog["review_levels"]["presets"]["medium"]["dimensions"]
