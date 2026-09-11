"""Strict, manifest-first GitHub snapshot importer."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .ingest import InputValidationError, ID_PATTERN, _safe_excerpt, _valid_datetime

HEX40 = re.compile(r"^[0-9a-f]{40}$")
SAFE_ID = re.compile(r"^[A-Z0-9]+(?:-[A-Z0-9]+)+$")
STATES = {"OPEN", "CLOSED", "MERGED", "APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"}

def _err(code: str, path: Path, field: str, message: str) -> InputValidationError:
    return InputValidationError(message, code=code, path=path, field=field)

def _strict(raw: bytes, path: Path) -> Any:
    try: text = raw.decode("utf-8")
    except UnicodeDecodeError as exc: raise _err("unsupported_encoding", path, "encoding", "input must be UTF-8") from exc
    if "\x00" in text: raise _err("invalid_json", path, "$", "NUL is not allowed")
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj: raise _err("invalid_json", path, "$", "duplicate JSON key")
            obj[key] = value
        return obj
    try: return json.loads(text, object_pairs_hook=pairs)
    except json.JSONDecodeError as exc: raise _err("invalid_json", path, "$", "invalid JSON") from exc

def _url(value: Any, repository_url: str, path: Path, field: str) -> str:
    if not isinstance(value, str): raise _err("invalid_github_snapshot", path, field, "URL is required")
    p, base = urlparse(value), urlparse(repository_url)
    if p.scheme != "https" or p.netloc != "github.com" or p.username or p.password or p.query or (p.fragment and not re.fullmatch(r"pullrequestreview-[0-9]+", p.fragment)) or p.path.rstrip("/").split("/")[:3] != base.path.rstrip("/").split("/")[:3]:
        raise _err("sensitive_content", path, field, "URL must be a safe repository URL")
    return value

def _text(value: Any, path: Path, field: str) -> str:
    if not isinstance(value, str) or not value.strip(): raise _err("invalid_github_snapshot", path, field, "required string")
    try: _safe_excerpt(value, path, field)
    except InputValidationError as exc: raise _err("sensitive_content", path, field, "unsafe text") from exc
    return value

def _base(item: dict, path: Path, fields: tuple[str, ...], repository_url: str) -> None:
    for field in fields:
        if field not in item: raise _err("invalid_github_snapshot", path, field, "required field")
    _url(item["url"], repository_url, path, "url")
    for field in ("createdAt", "updatedAt", "committedAt", "submittedAt"):
        if field in item and item[field] is not None: _valid_datetime(item[field], path, field)

def load_github_snapshot(sync_dir: str | Path) -> list[dict[str, Any]]:
    directory = Path(sync_dir); manifest_path = directory / "manifest.json"
    try: manifest = _strict(manifest_path.read_bytes(), manifest_path)
    except FileNotFoundError as exc: raise _err("invalid_manifest", manifest_path, "$", "manifest.json is required") from exc
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != "1.0": raise _err("invalid_manifest", manifest_path, "schemaVersion", "unsupported manifest")
    required = ("syncId", "repositoryId", "repositoryUrl", "snapshotFile", "sourceRevision", "retrievedAt", "updatedBy", "approvalStatus", "syncMode")
    for field in required:
        if field not in manifest: raise _err("invalid_manifest", manifest_path, field, "required field")
    if directory.name != manifest["syncId"] or not re.fullmatch(r"SYNC-[0-9]{8}T[0-9]{6}Z-[0-9]{6}", directory.name): raise _err("invalid_manifest", manifest_path, "syncId", "invalid sync id")
    if directory.parent.name != manifest["repositoryId"] or not SAFE_ID.fullmatch(directory.parent.name): raise _err("invalid_manifest", manifest_path, "repositoryId", "invalid repository id")
    if not isinstance(manifest["snapshotFile"], str) or Path(manifest["snapshotFile"]).name != manifest["snapshotFile"] or manifest["snapshotFile"].startswith("."): raise _err("invalid_manifest", manifest_path, "snapshotFile", "unsafe path")
    try: _valid_datetime(manifest["retrievedAt"], manifest_path, "retrievedAt")
    except InputValidationError as exc: raise _err("invalid_manifest", manifest_path, "retrievedAt", "invalid timestamp") from exc
    if manifest["approvalStatus"] not in {"proposed", "approved"} or manifest["syncMode"] not in {"incremental", "full"}: raise _err("invalid_manifest", manifest_path, "approvalStatus", "invalid value")
    repository_url = manifest["repositoryUrl"]
    if not isinstance(repository_url, str) or urlparse(repository_url).query or urlparse(repository_url).fragment or not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository_url.rstrip("/")): raise _err("invalid_manifest", manifest_path, "repositoryUrl", "invalid repository URL")
    snapshot_path = directory / manifest["snapshotFile"]
    try: raw = snapshot_path.read_bytes()
    except OSError as exc: raise _err("invalid_manifest", manifest_path, "snapshotFile", "snapshot is not readable") from exc
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(manifest["sourceRevision"])) or "sha256:" + hashlib.sha256(raw).hexdigest() != manifest["sourceRevision"]: raise _err("invalid_manifest", manifest_path, "sourceRevision", "digest mismatch")
    snapshot = _strict(raw, snapshot_path)
    if not isinstance(snapshot, dict) or set(snapshot) != {"issues", "pullRequests", "commits", "reviews"}: raise _err("invalid_github_snapshot", snapshot_path, "$", "allowlist mismatch")
    for key in snapshot:
        if not isinstance(snapshot[key], list): raise _err("invalid_github_snapshot", snapshot_path, key, "array required")
    records: list[dict[str, Any]] = []
    source_id = f"{manifest['repositoryId']}:{manifest['syncId']}"; retrieved = manifest["retrievedAt"]; status = manifest["approvalStatus"]
    def provenance(anchor, observed=retrieved):
        return {"source_id": source_id, "source_type": "github", "source_locator": f"inputs/github/{manifest['repositoryId']}/{manifest['syncId']}/snapshot.json", "source_anchor": anchor, "source_revision": manifest["sourceRevision"], "retrieved_at": retrieved, "updated_by": manifest["updatedBy"], "observed_at": observed, "extraction_method": "deterministic_import", "confidence": 1.0, "evidence_excerpt": "GitHub API snapshot"}
    def record(rid, title, typ, attrs, anchor, observed, relations):
        p = provenance(anchor, observed); records.append({"id": rid, "title": title, "type": typ, **attrs, "approvalStatus": status, "extractionMethod": "deterministic_import", "confidence": 1.0, "evidenceExcerpt": p["evidence_excerpt"], "provenance": p, "relations": relations})
    issue_ids = {}
    for i, item in enumerate(snapshot["issues"]):
        if not isinstance(item, dict): raise _err("invalid_github_snapshot", snapshot_path, f"issues[{i}]", "object required")
        _base(item, snapshot_path, ("number", "databaseId", "url", "title", "state", "createdAt", "updatedAt", "closedAt", "labels"), repository_url)
        if not isinstance(item["number"], int) or item["number"] <= 0 or not isinstance(item["databaseId"], (str, int)) or item["state"] not in {"OPEN", "CLOSED"} or not isinstance(item["labels"], list) or any(not isinstance(x, str) for x in item["labels"]): raise _err("invalid_github_snapshot", snapshot_path, f"issues[{i}]", "invalid issue")
        title = _text(item["title"], snapshot_path, f"issues[{i}].title"); rid = f"{manifest['repositoryId']}-ISSUE-{item['number']}"; issue_ids[item["number"]] = rid
        record(rid, title, "Issue", {"githubNumber": item["number"], "githubDatabaseId": str(item["databaseId"]), "githubState": item["state"], "githubCreatedAt": item["createdAt"], "githubUpdatedAt": item["updatedAt"], "githubClosedAt": item["closedAt"], "githubUrl": item["url"]}, f"$.issues[{i}]", item["updatedAt"], [])
    pr_ids = {}
    for i, item in enumerate(snapshot["pullRequests"]):
        _base(item, snapshot_path, ("number", "databaseId", "url", "title", "state", "createdAt", "updatedAt", "mergedAt", "closedAt", "headSha", "mergeCommitSha", "linkedIssueNumbers"), repository_url)
        if not isinstance(item["number"], int) or not isinstance(item["databaseId"], (str, int)) or item["state"] not in {"OPEN", "CLOSED", "MERGED"} or not isinstance(item["linkedIssueNumbers"], list) or any(not isinstance(x, int) or x not in issue_ids for x in item["linkedIssueNumbers"]): raise _err("unknown_target", snapshot_path, f"pullRequests[{i}].linkedIssueNumbers", "unknown issue")
        if not isinstance(item["headSha"], str) or not HEX40.fullmatch(item["headSha"]) or (item["mergeCommitSha"] is not None and (not isinstance(item["mergeCommitSha"], str) or not HEX40.fullmatch(item["mergeCommitSha"]))): raise _err("invalid_github_snapshot", snapshot_path, f"pullRequests[{i}]", "invalid SHA")
        rid = f"{manifest['repositoryId']}-PR-{item['number']}"; pr_ids[item["number"]] = rid; relations = [{"type": "RELATES_TO", "to": issue_ids[n], "evidenceExcerpt": "GitHub structured issue link", "provenance": provenance(f"$.pullRequests[{i}].linkedIssueNumbers[{j}]")} for j, n in enumerate(item["linkedIssueNumbers"])]
        record(rid, _text(item["title"], snapshot_path, f"pullRequests[{i}].title"), "PullRequest", {"githubNumber": item["number"], "githubDatabaseId": str(item["databaseId"]), "githubState": item["state"], "githubCreatedAt": item["createdAt"], "githubUpdatedAt": item["updatedAt"], "githubMergedAt": item["mergedAt"], "githubClosedAt": item["closedAt"], "headSha": item["headSha"], "mergeCommitSha": item["mergeCommitSha"], "githubUrl": item["url"]}, f"$.pullRequests[{i}]", item["updatedAt"], relations)
    commit_ids = {item.get("sha"): f"{manifest['repositoryId']}-COMMIT-{str(item.get('sha','')).upper()[:16]}" for item in snapshot["commits"] if isinstance(item, dict)}
    for i, item in enumerate(snapshot["commits"]):
        _base(item, snapshot_path, ("sha", "url", "committedAt", "parentShas", "pullRequestNumbers"), repository_url)
        if not HEX40.fullmatch(item["sha"]) or not isinstance(item["parentShas"], list) or any(not isinstance(x, str) or not HEX40.fullmatch(x) for x in item["parentShas"]) or not isinstance(item["pullRequestNumbers"], list) or any(not isinstance(x, int) or x not in pr_ids for x in item["pullRequestNumbers"]): raise _err("invalid_github_snapshot", snapshot_path, f"commits[{i}]", "invalid commit")
        rid = commit_ids[item["sha"]]; relations = [{"type": "RELATES_TO", "to": pr_ids[n], "evidenceExcerpt": "GitHub structured pull request association", "provenance": provenance(f"$.commits[{i}].pullRequestNumbers")} for n in item["pullRequestNumbers"]]
        relations += [{"type": "DERIVED_FROM", "to": commit_ids[p], "evidenceExcerpt": "GitHub structured parent commit", "provenance": provenance(f"$.commits[{i}].parentShas")} for p in item["parentShas"] if p in commit_ids]
        record(rid, "GitHub commit", "Commit", {"githubSha": item["sha"], "githubCommittedAt": item["committedAt"], "githubUrl": item["url"]}, f"$.commits[{i}]", item["committedAt"], relations)
    for i, item in enumerate(snapshot["reviews"]):
        _base(item, snapshot_path, ("databaseId", "pullRequestNumber", "url", "state", "submittedAt", "commitSha"), repository_url)
        if not isinstance(item["pullRequestNumber"], int) or item["pullRequestNumber"] not in pr_ids or item["state"] not in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"} or not isinstance(item["commitSha"], str) or not HEX40.fullmatch(item["commitSha"]): raise _err("unknown_target", snapshot_path, f"reviews[{i}]", "unknown pull request or invalid commit")
        database_id = str(item["databaseId"])
        if not re.fullmatch(r"[A-Za-z0-9-]+", database_id): raise _err("invalid_github_snapshot", snapshot_path, f"reviews[{i}].databaseId", "invalid database id")
        rid = f"{manifest['repositoryId']}-REVIEW-{database_id.upper()}"; relations = [{"type": "VERIFIES", "to": pr_ids[item["pullRequestNumber"]], "evidenceExcerpt": "GitHub review record", "provenance": provenance(f"$.reviews[{i}]")}]
        record(rid, "GitHub PR review", "SourceDocument", {"githubDatabaseId": str(item["databaseId"]), "reviewState": item["state"], "githubSubmittedAt": item["submittedAt"], "commitSha": item["commitSha"], "githubUrl": item["url"]}, f"$.reviews[{i}]", item["submittedAt"], relations)
    source_relations = []
    for record_item in records: record_item["relations"].append({"type": "DERIVED_FROM", "to": f"{manifest['repositoryId']}-SYNC-{manifest['syncId']}", "evidenceExcerpt": "GitHub snapshot source", "provenance": provenance(record_item["provenance"]["source_anchor"] + ":source")})
    record(f"{manifest['repositoryId']}-SYNC-{manifest['syncId']}", "GitHub snapshot manifest", "SourceDocument", {}, "$", retrieved, [])
    return records

load_github_snapshots = lambda root: [r for d in sorted(Path(root).glob("*/SYNC-*")) for r in load_github_snapshot(d)]
