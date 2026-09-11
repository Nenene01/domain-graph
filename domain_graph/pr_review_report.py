"""Non-mutating JSON/Markdown PR report renderers and deny-by-default publisher."""
from __future__ import annotations
import json


def render_json(report) -> str:
    value = report.as_dict() if hasattr(report, "as_dict") else report
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def render_markdown(report) -> str:
    value = report.as_dict() if hasattr(report, "as_dict") else report
    lines = ["# Consistency report", "", f"- Status: `{value.get('status', 'unknown')}`", f"- Run: `{value.get('runId', '')}`", "", "## Findings", ""]
    for finding in value.get("findings", []):
        subject = finding.get("subject", {}).get("id", "unknown")
        lines.append(f"- `{finding.get('severity')}` `{finding.get('ruleId')}` `{subject}` — `{finding.get('state')}`")
    if not value.get("findings"): lines.append("No findings.")
    return "\n".join(lines) + "\n"


class GitHubPublisher:
    def publish(self, *args, **kwargs):
        raise PermissionError("approval_required")


def publisher_allowed(*args, **kwargs) -> bool: return False
