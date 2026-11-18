"""Complexity metrics (fan-in/fan-out) and AppSec SARIF/JSON telemetry overlay."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from aegis_code_graph.models import SecurityFlag, SeverityType, VerifiedCallEdge


def compute_fan_metrics(
    verified_edges: list[VerifiedCallEdge],
) -> tuple[dict[str, int], dict[str, int]]:
    """Compute per-file fan_in and fan_out counts from deterministic import edges."""
    fan_in: dict[str, int] = defaultdict(int)
    fan_out: dict[str, int] = defaultdict(int)
    for edge in verified_edges:
        fan_out[edge.source_file] += 1
        fan_in[edge.target_file] += 1
    return dict(fan_in), dict(fan_out)


def _normalize_severity(raw: str | None) -> SeverityType:
    """Map SARIF / Semgrep / MobSF severity labels to SeverityType."""
    val = (raw or "medium").strip().lower()
    if val in {"critical", "crit"}:
        return "critical"
    if val in {"high", "error"}:
        return "high"
    if val in {"medium", "warning", "warn"}:
        return "medium"
    return "low"


def _normalize_report_path(raw_uri: str, repo_root: Path, valid_files: set[str]) -> str | None:
    """Resolve a URI/path from a security report to a repository-relative POSIX path."""
    cleaned = raw_uri.strip()
    if cleaned.startswith("file://"):
        cleaned = cleaned[len("file://") :]
    cleaned = cleaned.replace("\\", "/").lstrip("./")

    root_posix = repo_root.resolve().as_posix().rstrip("/") + "/"
    if cleaned.startswith(root_posix):
        cleaned = cleaned[len(root_posix) :]

    if cleaned in valid_files:
        return cleaned

    # Suffix fallback when report includes an outer container path
    for vf in valid_files:
        if cleaned.endswith("/" + vf) or vf.endswith("/" + cleaned):
            return vf
    return None


def _parse_sarif(data: dict[str, Any], repo_root: Path, valid_files: set[str]) -> dict[str, list[SecurityFlag]]:
    """Parse SARIF 2.1.0 runs[].results[]."""
    findings: dict[str, list[SecurityFlag]] = defaultdict(list)
    for run in data.get("runs", []):
        rules_sev: dict[str, str] = {}
        driver = run.get("tool", {}).get("driver", {})
        for rule in driver.get("rules", []):
            rid = rule.get("id")
            lvl = rule.get("defaultConfiguration", {}).get("level")
            if rid and lvl:
                rules_sev[rid] = lvl

        for res in run.get("results", []):
            rule_id = res.get("ruleId") or "unknown-rule"
            level = res.get("level") or rules_sev.get(rule_id)
            severity = _normalize_severity(level)
            message = res.get("message", {}).get("text") or rule_id

            for loc in res.get("locations", []):
                phys = loc.get("physicalLocation", {})
                uri = phys.get("artifactLocation", {}).get("uri")
                if not uri:
                    continue
                rel = _normalize_report_path(uri, repo_root, valid_files)
                if not rel:
                    continue
                line = int(phys.get("region", {}).get("startLine") or 0)
                findings[rel].append(
                    SecurityFlag(
                        rule_id=str(rule_id),
                        severity=severity,
                        line=line,
                        message=str(message)[:240],
                    )
                )
    return dict(findings)


def _parse_semgrep_or_list(
    data: Any,
    repo_root: Path,
    valid_files: set[str],
) -> dict[str, list[SecurityFlag]]:
    """Parse Semgrep JSON (`results[]`) or Gitleaks-style list reports."""
    findings: dict[str, list[SecurityFlag]] = defaultdict(list)
    items = data.get("results", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

    for item in items:
        if not isinstance(item, dict):
            continue
        raw_path = item.get("path") or item.get("File") or item.get("file")
        if not raw_path:
            continue
        rel = _normalize_report_path(str(raw_path), repo_root, valid_files)
        if not rel:
            continue
        extra = item.get("extra", {}) if isinstance(item.get("extra"), dict) else {}
        rule_id = item.get("check_id") or item.get("RuleID") or item.get("rule_id") or "security-finding"
        sev_raw = extra.get("severity") or item.get("severity")
        line = int(
            (item.get("start", {}).get("line") if isinstance(item.get("start"), dict) else None)
            or item.get("StartLine")
            or item.get("line")
            or 0
        )
        msg = extra.get("message") or item.get("Description") or item.get("message") or str(rule_id)
        findings[rel].append(
            SecurityFlag(
                rule_id=str(rule_id),
                severity=_normalize_severity(str(sev_raw) if sev_raw else None),
                line=line,
                message=str(msg)[:240],
            )
        )
    return dict(findings)


def load_security_findings(
    report_path: str | Path | None,
    repo_root: Path,
    valid_files: list[str] | set[str],
) -> dict[str, list[SecurityFlag]]:
    """Load AppSec findings from a SARIF or Semgrep/Gitleaks JSON file."""
    if not report_path:
        return {}
    path = Path(report_path)
    if not path.is_file():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return {}

    valid_set = set(valid_files)
    if isinstance(data, dict) and "runs" in data:
        return _parse_sarif(data, repo_root, valid_set)
    return _parse_semgrep_or_list(data, repo_root, valid_set)
