"""Release hygiene: one version, one platform claim.

These are cheap checks that fail at exactly the moment a release drifts -- a
bumped pyproject with a stale `plugin.yaml`, or a manifest that claims a platform
no skill has been written for.
"""

import re
import tomllib
from pathlib import Path

import yaml

import pitwall

ROOT = Path(__file__).resolve().parent.parent


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    assert match, f"{path} has no frontmatter"
    return yaml.safe_load(match.group(1)) or {}


def test_the_three_versions_agree():
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["version"] == project["project"]["version"] == pitwall.__version__


def test_the_manifest_and_the_skills_claim_the_same_platforms():
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    declared = set(manifest["platforms"])
    for skill in sorted((ROOT / "skills").glob("*/SKILL.md")):
        platforms = set(_frontmatter(skill).get("platforms") or [])
        assert platforms == declared, f"{skill.parent.name} claims {sorted(platforms)}"


def test_the_readme_lists_every_tool():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    missing = [tool for tool in manifest["provides_tools"] if f"`{tool}`" not in readme]
    assert not missing, f"README does not mention {missing}"
