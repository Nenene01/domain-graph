# Issue #3: 設計・実装不整合の検出と PR レビュー連携設計

## 結論・根拠・対象境界

GitHub Issue #3 の実際の題名は「設計・実装不整合の検出とPRレビュー連携を実装する」である。本設計は、取り込み済みの**承認済みかつ provenance を持つ事実**を固定 Cypher で照合し、未対応要件・孤立設計・出所不足・関係矛盾を「要確認 findings」として返す read-only 検査を定義する。AI は候補の説明や優先順位付けだけを `ai_inferred` / `proposed` として別に扱い、事実・承認・GitHub への変更を自動で行わない。

| 根拠 | 設計への反映 |
| --- | --- |
| `AGENTS.md`、`README.md`、`PLAN.md` | provenance、事実と AI の分離、未登録/根拠不足の明示、冪等性、秘密/PII 非保存、承認のない自動変更禁止。 |
| #7 と `ingest.py` / `neo4j_store.py` | `DomainNode.id`、`RELATION.key=from|type|to`、8語彙、relation ごとの provenance、競合時の `conflicting_record` を変更しない。 |
| #9 の query API | 承認済み・根拠付きのみを既定にし、固定・パラメータ化 Cypher、`not_registered` / `evidence_insufficient`、safe error、`graph_trace` 監査と整合させる。 |
| #4 の MCP | MCP は既存の3 read tool のみ。`write-propose` / `write-apply` を持つ token でも初期公開 tool に書込みを追加しない。将来の適用は proposal digest と人間の one-time approval を要する。 |
| #5 | 更新を後勝ちで解決せず、同一 ID/key の異内容は conflict とし、原文・個人情報を返さない。 |
| #6 と `github_import.py` / `github_sync.py` | GitHub は read-only snapshot から入り、Issue/PR/commit/review は既定 `proposed`。token、本文、メール、header を snapshot・graph・audit に保存しない。 |

対象は、検出契約、固定 query、PR 用の非破壊レポート、監査、差分再実行、テストである。入力スキーマ、既存取込・query API/MCP の公開契約、Neo4j の既存データを変更しない。GitHub のコメント投稿、review submit、label/merge/close、ライブ GitHub API 取得、AI による関係の自動確定は対象外である。

## データの分類と判定原則

検査対象（fact set）は node と relation の双方が `approvalStatus=approved`、`extractionMethod != ai_inferred`、空でない `evidenceExcerpt`、必須 provenance（source ID/locator/anchor/retrievedAt/updatedBy/observedAt）を持つものに限定する。`proposed`、`rejected`、`superseded`、AI 推論、#6 の未承認 GitHub snapshot は、既定 findings の存在・解消根拠に使わない。

`includeProposed=true` は `domain-graph:read-proposed` を明示的に持つローカル/CI 利用者だけに許す。この場合も提案データを `candidateEvidence` として別配列に返し、finding の state を `evidence_insufficient` のまま自動解消しない。AI の説明は `inference`（model/version、prompt digest、input relation keys、confidence、短い安全な rationale）として report 内だけに保持し、`fact`、既存 `DomainNode`、`RELATION` を更新しない。

confidence は真偽の確率ではなく、検出の再現性・根拠の完全性の指標である。決定的ルールの初期値は、完全な approved evidence で `1.00`、候補 evidence のみで `0.00`（ただし candidate を提示可能）、競合/欠損 provenance で `0.00` とする。AI 補助値は 0.00〜1.00 で表示専用とし、severity、failure、PR 投稿可否を単独で変えてはならない。

## finding 契約、重大度、absence

新規の内部 DTO は `ConsistencyCheckRequest` と `ConsistencyReport` とする。ID は既存 `ID_PATTERN`、relation type は既存8語彙だけを受け、任意 Cypher、ラベル、property、パスを受け取らない。request は `repositoryId`、`pullRequestId`（任意）、`headSha`（任意）、`baselineRevision`、`scopeNodeIds`（最大100）、`includeProposed=false`、`maxFindings=200` だけを許可する。PR snapshot がない場合も local scope のレポートは可能だが、PR 固有の結論を推測しない。

```json
{
  "status": "ok",
  "runId": "CONSISTENCY-<opaque>",
  "basis": {"sourceRevision": "sha256:...", "includeProposed": false},
  "findings": [{
    "fingerprint": "sha256:<rule|subject|expected|basis の digest>",
    "ruleId": "requirement_without_implementation",
    "severity": "warning",
    "state": "open",
    "subject": {"id": "REQ-ORDER-001", "type": "Requirement"},
    "expected": "approved implementation path",
    "evidence": [{"relationKey": "...", "sourceId": "...", "sourceLocator": "...", "sourceAnchor": "..."}],
    "confidence": 1.0,
    "verification": "approved Code/Design から Requirement への IMPLEMENTS 経路を確認する"
  }],
  "summary": {"error": 0, "warning": 1, "info": 0, "suppressed": 0}
}
```

`not_registered` は検査起点（scope node / PR ID）が graph に無い場合だけで、HTTP/CLI とも正常な空 report とする。開始点が登録済みでも必要な approved evidence がない、候補だけ、対象が未承認、provenance が不足している場合は `evidence_insufficient` finding を返す。「不整合である」とは断定しない。互いに異なる approved provenance が同一 ID/key に存在する場合は既存取込が拒否するが、既存 DB から発見した場合は `conflicting_evidence` error finding として判断を保留する。

| ruleId | 条件（すべて approved fact set） | severity | FP 抑制・確認 |
| --- | --- | --- | --- |
| `requirement_without_implementation` | Requirement から Design/Code へ、保存向きを保った `IMPLEMENTS` の逆探索を最大6 hopしても到達しない | warning | `proposed` のみは解消扱いにせず candidate に表示。Requirement が `superseded/rejected` なら対象外。 |
| `design_without_implementation` | Design から Code へ `IMPLEMENTS` の逆探索で到達しない | warning | Code node の有無だけでなく relation evidence を必須にする。設計の対象外/廃止状態を勝手に解釈しない。 |
| `implementation_without_requirement` | Code/Feature から Requirement/Business へ保存向き `IMPLEMENTS` を最大6 hopしても到達しない | warning | GitHub commit/PR の `RELATES_TO` は要件実装根拠に代用しない。対象外コードの allowlist は人間承認済み設定のみ。 |
| `missing_provenance` | 対象 node/relation の必須 provenance、evidence excerpt、approval/extraction/confidence のいずれかが欠ける | error | 値を補完しない。安全な ID/field 名だけを表示する。 |
| `relation_direction_or_type_conflict` | 同一 subject/target に、#7 の語彙・向きと矛盾する approved assertion 又は逆向き二重登録がある | error | `RELATES_TO` は原文主語方向のみ。意味の異なる2 relation を矛盾と推測しない。 |
| `conflicting_evidence` | 同じ ID/key について source revision / approval / evidence が相互に両立せず、一意な approved fact を選べない | error | 最新日時・PR番号・AI score による後勝ちは禁止。人間判断まで blocker として残す。 |

severity は `error`（provenance欠損/競合でレビュー判断不能）、`warning`（対応経路が未確認）、`info`（candidate、scope外、抑制通知）のみ。CI の失敗閾値は request/CI 設定の固定 enum `error_only`（既定）/`warning_or_error` とし、AI confidence で変更しない。baseline に同一 fingerprint がある未解消 finding は `existing`、head の source revision・対象 relation key・rule が同じなら `unchanged` とし、新規/悪化だけを PR summary の failure 候補にする。

## 実行、固定 query、差分・冪等性

実装は `domain_graph/consistency_contract.py`（DTO/validator/fingerprint/safe errors）、`consistency_repository.py`（read-only fixed Cypher）、`consistency_service.py`（rule orchestration、差分、audit）、`pr_review_report.py`（JSON/Markdown renderer）に分離する。既存 `query_contract.py`、`query_service.py`、`mcp_contract.py` の request/response は変更しない。初期は CLI/CI が service を直接呼び、MCP tool を追加しない。

repository は (1) 起点存在、(2) approved node/relation の許可属性 projection、(3) `IMPLEMENTS` 到達、(4) provenance 完全性、(5) 同一 key/id の矛盾候補、の固定テンプレートだけを持つ。全値はパラメータ化し、可変長は `*1..6` 固定後に `$maxDepth` で絞る。tenant-aware repository が用意されるまで shared DB 実行を拒否し、#4 と同じ tenant/project filter を query 内の最初の `MATCH` から強制する。deadline は DB 2秒、service 3秒、候補総数10,000、finding 200、scope 100 とし、超過は `query_limit_exceeded` / `query_timeout` で部分的な「合格」を返さない。

run identity は canonical request、approved graph basis（snapshot digest 又は安定した revision）、rule version、PR head SHA の SHA-256 とする。同一 identity の再実行は同じ fingerprint・安定順序（`severity, ruleId, subject.id, fingerprint`）の report を返し、graph を書かず、PR に二重投稿しない。新しい snapshot/digest は新 run だが、元の snapshot や以前の report を上書き・削除しない。GitHub が削除/force-push/関連解除されたことは #6 の範囲外なので、absence を自動修復・閉鎖の根拠にしない。

## PR レビュー連携と承認境界

CI integration は #6 が保存した PR/commit snapshot と人間承認済み Requirement/Design/Code の intersection を入力に、`report.json` と安全な `report.md` artifact を作るだけの read-only worker とする。PR number/head SHA が proposed snapshot にしか無い場合、report は `githubSnapshotApproval=proposed` と明示し、GitHub review の APPROVED state を設計承認・検出解消と解釈しない。

GitHub へ status/comment/review を投稿する publisher は本 Issue の実装対象外で、初期は**deny by default**である。将来追加する場合も、read-only 検査と別 process/credential に分離し、次のすべてを満たす時だけ一回投稿する: 人間が rendered report、PR repository/number/head SHA、report digest、投稿文 digest を別 UI で承認し、5分以下の one-time approval token を発行すること、`write-apply` scope、repository allowlist、head SHA 再照合、idempotency key が一致すること。期限切れ・再利用・head変更・tenant不一致・API失敗は投稿ゼロとし、MCP は承認を自己申告する入力も GitHub credential へのアクセスも受け取らない。

GitHub token は外部 secret manager から publisher の HTTP request 時だけ渡す短期・最小権限 credential とし、snapshot、Graph、report、audit、例外、環境表示に保存しない。report には Issue/PR title、body、comment、commit message、author/email、URL query/fragment、token を出さず、安全な ID、canonical locator、anchor、digest、rule/resultだけを出す。

## エラー・監査

外部応答は `{"error":{"code":"...","message":"..."},"meta":{"requestId":"..."}}` の安全な固定形にする。許可 code は `invalid_request`、`unauthorized`、`forbidden`、`not_registered`、`evidence_insufficient`、`query_limit_exceeded`、`query_timeout`、`service_unavailable`、`conflicting_evidence`、`approval_required`、`approval_invalid`、`github_publish_failed` とする。token、Cypher、入力値、本文、絶対パス、stack trace、メールを message に含めない。

成功・失敗・抑制・publish拒否ごとに一件の `consistency_check` audit event を記録する。許可属性は `occurredAt`、`requestId`、`runId`、`repositoryId`、`pullRequestIdPresent`、`headShaDigest`、`basisRevision`、`ruleVersion`、`includeProposed`、rule別件数、severity別件数、outcome、errorCode、durationMs、publisherAttempted、approvalOutcome のみとする。#6 の `github_sync`、#9 の `graph_trace`、#4 の `mcp_tool_call` は変更せず request ID で相関する。監査 sink の保持・暗号化・閲覧権限は未登録であり、`MemoryAudit` を production に使わない。

## Luna 向け実装順序・受入観測

1. 先に本書、`AGENTS.md`、#4/#5/#6/#7/#9、`ingest.py`、`github_import.py`、`query_repository.py`、`mcp_service.py` を読む。既存 public API/取込テストを変更しない。
2. 上記4新規モジュールと `tests/test_consistency_*.py` を追加する。Neo4j driver を持たない in-memory repository を注入可能にし、固定 Cypher は repository に閉じる。
3. approved sample の `REQ-ORDER-001 → DSN-ORDER-001 ← CODE-ORDER-001`、proposed GitHub snapshot、AI candidate、欠損 provenance、逆向き/競合を安全な fixture で作る。fixture に実 token、メール、本文、実在 secret を置かない。
4. `requirement_without_implementation`、`design_without_implementation`、`implementation_without_requirement`、`missing_provenance`、`relation_direction_or_type_conflict`、`conflicting_evidence` の各 rule、not_registered/evidence_insufficient、confidence/候補分離、fingerprint の安定性、同一 run の no-op、basis差分時の new/existing 判定をテストする。
5. MCP の `tools/list` が従来3件のまま、write scope を持っても graph/node/edge 数が不変、GitHub publisher が approval 無しで呼べないことを回帰テストする。固定 query に Cypher fragment、巨大 scope、未知 field を渡しても safe error・任意 Cypher 非実行を確認する。
6. `python3 -m unittest discover -v`、`npm test`、`npm run lint`、`npm run build`、可能なら `RUN_NEO4J_TESTS=true npm run test:neo4j` を実行し、変更ファイル・観測・未決定事項を報告する。

受入は、同一 approved basis の再実行で finding/report digest と graph 件数が不変であること、proposed/AI だけで warning を解消しないこと、各 finding が node/relation の source locator/anchor に遡れること、競合を保留すること、PR 連携が report 作成だけで GitHub を変更しないこと、秘密/PII が graph・report・error・audit に無いことで判定する。

## 実装前の決定待ち

tenant/project を graph に永続化して固定 query 全経路で強制する方法、Assertion/versioned assertion による複数根拠・履歴、CI の baseline 保管場所/保持期間、抑制の承認者・期限・保存先、GitHub publisher の credential 発行者と承認 UI/one-time token issuer は未登録である。これらが決まるまで、shared DB の検査、抑制の自動永続化、既存 fact の更新、GitHub 投稿を実装してはならない。
