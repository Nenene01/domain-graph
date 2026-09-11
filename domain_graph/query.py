"""Queries deliberately return explicit absence statuses."""
from .ingest import InMemoryGraph


def trace(graph: InMemoryGraph, requirement_id: str, target_id: str | None = None):
    result = graph.trace(requirement_id, target_id)
    if result["status"] == "ok" and not result["relations"]:
        result["status"] = "evidence_insufficient"
    return result
