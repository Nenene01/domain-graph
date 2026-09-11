# Vendor-neutral closed-cloud skeleton

This directory is intentionally provider-neutral. `manifest.example.yaml` contains
references only; it is not a production configuration. Bind cloud, region, network,
IdP, KMS, retention, and endpoints from an approved environment decision record before
planning or applying. Any missing or undecided value must fail validation.

The skeleton requires: private-only Neo4j/Bolt and workers, a TLS/authenticated gateway
as the only client boundary, separate runtime/ingest/migration/backup identities,
default-deny ingress/egress, encrypted storage, durable audit, and immutable artifact
digests. Provider modules must implement plan/apply separately and must never put secret
values in state.
