from __future__ import annotations
from typing import Protocol
class AuditSink(Protocol):
    def record(self, event: dict): ...
class NullAudit:
    def record(self, event: dict): return None
class MemoryAudit:
    def __init__(self): self.events=[]
    def record(self,event): self.events.append(dict(event))
