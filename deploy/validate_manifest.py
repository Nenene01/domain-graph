#!/usr/bin/env python3
"""Validate a deployment manifest without resolving secrets or external endpoints."""
import json, sys
from pathlib import Path
from domain_graph.operations import DeploymentConfig, ConfigurationError

def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_manifest.py PATH", file=sys.stderr); return 2
    try:
        import yaml
    except ImportError:
        print("yaml parser unavailable; use the platform validator", file=sys.stderr); return 2
    try:
        raw = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
        config = DeploymentConfig.from_mapping(raw, production=True)
        print(json.dumps({"valid": True, "configDigest": config.digest}))
        return 0
    except (OSError, ConfigurationError, TypeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)})); return 1
if __name__ == "__main__": raise SystemExit(main())
