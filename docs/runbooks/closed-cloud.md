# 閉じたクラウド運用 runbook（dry-run 対応）

## Deploy

1. 承認済み decision record と非秘密 manifest を照合し、`deploy/validate_manifest.py` を実行する。
2. immutable image/artifact digest の IaC plan、scan、migration plan をレビューする（未決定値・secret があれば中止）。
3. 承認済み job identity で plan/apply を分離し、private network、default deny、TLS gateway、Neo4j 非公開を確認する。
4. readiness、認可済み代表 trace、監査 enqueue、rate-limit を dry-run/隔離環境で確認して段階切替する。

## Rollback / migration

Traffic を直前の互換 artifact/config に戻す。graph、入力原本、provenance、migration history、backup は削除しない。
Migration は停止・状態検証後に forward fix または restore を承認し、既存 migration の書換えや履歴破壊をしない。

## Backup / restore drill

Write/ingest/migration を止め、catalog の digest・artifact digest・migration version を照合する。隔離環境へ restore し、constraint、node/relation 件数、provenance、代表 trace を検証してから切替を承認する。既存 DB の上書き復旧は禁止。

## Incident / credential rotation

readiness を閉じ、影響 identity を失効・rotation し、監査へ request ID・identity・結果だけを記録する。token、URI、本文、PII、stack trace は記録しない。原因、影響範囲、再発防止、承認者を decision/audit record に残す。
