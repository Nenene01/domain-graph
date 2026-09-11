# Issue #5: 課題管理表 CSV/JSON ローカルインポート契約

## 決定と対象範囲

スプレッドシートからローカルに出力した CSV 又は JSON を、明示的なマニフェストで
列・パス対応付けして取り込む。各行（JSON では各要素）は `Issue` の `DomainNode`、
エクスポート自体は `SourceDocument` の `DomainNode`、担当・関連要件・原資料への参照は
既存8語彙の `RELATION` に正規化する。期限と状態は Issue ノードの正規化済みスカラー
属性 `dueDate` と `issueStatus` とし、原文の値は保存しない。

対象は `inputs/issue-trackers/` 内のローカルファイルの検証・正規化・既存 Python
取込への接続である。外部サービス、ライブ API、クラウド、列名の推測、AI による補完、
課題の自動更新・クローズは対象外である。原ファイルは読み取り専用であり、取込処理は
上書き・整形・削除を行わない。

## 根拠と維持する契約

| 根拠 | 本設計への反映 |
| --- | --- |
| `README.md` の Phase 2 と開発方針 | 課題管理表をローカル・出所付き・冪等に取り込む。秘密情報を保存しない。 |
| `PLAN.md` の Phase 2/共通受入条件 | CSV/JSON は Phase 2 の作業であり、再現可能な fixture とテストを追加する。 |
| Issue #7 | `:DomainNode {id}`、`:RELATION {key}`、8語彙、関係ごとの provenance、承認/AI 分離を維持する。 |
| Issue #8 と `domain_graph/ingest.py` | snake_case provenance、機械可読 `InputValidationError`、UTF-8、安全な locator、未知端点の全体ロールバックを再利用する。 |
| Issue #11 相当の現行実装（`f19f871`） | 厳格 provenance、承認済み人間定義を未承認/AI値で上書きしないこと、関係端点検査を維持する。 |

`id` はグローバル一意で不変、関係キーは必ず `from|type|to` である。既存の
`meeting-notes`、`requirements`、`design`、`source-metadata` のファイル形式・ID・
provenance map を変更しない。Node.js は接続基盤のままとし、Python の取込を重複実装しない。

## 入力配置、原文保存、マニフェスト

一エクスポートを一ディレクトリに隔離する。`<export-id>` は `SRC-` を含む既存 ID 規則
（大文字英数字とハイフン）に従う、プロジェクト内で一意な不変 ID とする。

```text
inputs/issue-trackers/
  SRC-TRACKER-20260912/
    manifest.json          # 人間が承認する変換設定
    original.csv           # 又は original.json。元データ、取込中は絶対に変更しない
```

`manifest.json` は UTF-8（BOM 不可）の単一 JSON object、`schemaVersion: "1.0"` とする。
CSV/JSON はマニフェストの `originalFile` で一つだけ指定する。CSV は UTF-8、UTF-8 BOM は
先頭の一回だけ許可し除去して読む。LF と CRLF は許可するが、CSV 内の引用符付き改行は RFC 4180
相当の CSV reader で一レコードとして扱う。UTF-16/Shift_JIS、途中 BOM、NUL、壊れた引用符、
不正な JSON、混在改行を理由にレコード境界を推測して復旧してはならず失敗する。

マニフェストは少なくとも次を持つ。`columnMap` の値は CSV の**完全一致ヘッダ名**、JSON の
`recordMap` の値は各 record object の**完全一致キー**である。別名・大文字小文字・空白の
正規化、値からの列探索はしない。JSON はトップレベル配列、又は `recordsPath` が指定する
トップレベル object 配下の配列だけを許可し、各要素は object とする。

```json
{
  "schemaVersion": "1.0",
  "exportId": "SRC-TRACKER-20260912",
  "originalFile": "original.csv",
  "format": "csv",
  "encoding": "utf-8",
  "delimiter": ",",
  "sourceRevision": "sha256:<verified-digest>",
  "retrievedAt": "2026-09-12T00:00:00Z",
  "observedAt": "2026-09-12T00:00:00Z",
  "updatedBy": "issue-tracker-exporter",
  "approvalStatus": "approved",
  "columnMap": {
    "issueId": "課題ID", "title": "件名", "status": "状態",
    "dueDate": "期限", "assigneeId": "担当ID", "requirementIds": "関連要件ID"
  },
  "statusMap": {"未着手": "open", "対応中": "in_progress", "完了": "done"},
  "requirementIdSeparator": ";"
}
```

JSON 版は `format: "json"`、`recordMap` を用い、`recordsPath`（例: `$.issues`）を明示する。
マニフェスト、原文、変換済みレコードの provenance はすべて同じ `exportId` と安全な相対
locator を使う。原文の完全内容はグラフ、エラー、ログ、テスト出力へ複写せず、リポジトリ上の
原文と `sourceRevision` の digest で対応を保つ。

## 列マッピングとグラフ対応

すべてのレコードは `extractionMethod=deterministic_import` である。マニフェスト承認者が
内容を人間確認した場合だけ `approvalStatus=approved`、それ以外は `proposed` とする。CSV/JSON
の値から AI 推論を作らない。AI が後に候補関係を追加する場合は別 assertion として
`ai_inferred` / `proposed` に限り、本インポート結果や承認済み定義を置換しない。

| 論理項目 | 必須 | 入力・検証 | 正規化先 |
| --- | --- | --- | --- |
| `issueId` | 必須 | 空白不可、ID 規則を満たす。CSV/JSON 内で一意。 | `DomainNode {id: issueId, type: Issue}` |
| `title` | 必須 | 空白不可、安全な最小文字列。 | `Issue.title` と `evidenceExcerpt`（安全な短い要約を manifest が指定、又は title の安全値）。 |
| `status` | 必須 | `statusMap` の完全一致キー。マップ先は `open` / `in_progress` / `blocked` / `done` / `cancelled` のみ。 | `Issue.issueStatus` |
| `dueDate` | 任意 | 空欄は `null`。`YYYY-MM-DD` の実在日、又は UTC `YYYY-MM-DDTHH:MM:SSZ` のみ。 | `Issue.dueDate`（日付は `T00:00:00Z` に正規化してよい） |
| `assigneeId` | 任意 | 空欄は未設定。人名・メール・電話番号ではなく、事前登録済みの安全な不透明 ID のみ。 | `Issue -[:RELATION {type: RELATES_TO}]-> Entity` |
| `requirementIds` | 任意 | 空欄は関係なし。明示 separator で分割後、空要素なし、重複なし、既存又は同一バッチの `Requirement` ID。 | `Issue -[:RELATION {type: RELATES_TO}]-> Requirement` |

各 export について `SourceDocument {id: exportId, type: SourceDocument}` を作り、各 Issue から
`DERIVED_FROM` を一件張る。`SourceDocument` の title は「課題管理表エクスポート」のような
安全な固定名で、原文 filename、checksum、表の全内容を title/evidence に含めない。

担当 `Entity` はこの Issue の範囲では**作成しない**。別の承認済み入力に存在する安全な担当
識別子への参照だけを許すため、氏名表示列だけしかない export は `pii_or_unsafe_assignee` で
失敗する。これは担当を黙って PII としてグラフ化しないためである。`RELATES_TO` は原文上の
主語である Issue から一方向だけ保存し、担当・要件から逆向き edge を生成しない。

| 8語彙 | このインポートでの使用 | 向き |
| --- | --- | --- |
| `DERIVED_FROM` | 必須: 課題が export 原文に由来する | `Issue → SourceDocument` |
| `RELATES_TO` | 任意: 担当 Entity、関連 Requirement | `Issue → Entity/Requirement` |
| `BLOCKS` | 任意の明示 `blockedIssueIds` マップを将来追加した時だけ | `Issue → Issue` |
| `DEFINED_BY`, `IMPLEMENTS`, `VERIFIES`, `CHANGED_BY`, `DECIDED_IN` | CSV/JSON の標準列からは作らない | Issue #7 の定義のまま |

`BLOCKS` や他の関係を status、期限、タイトル文言から推測してはならない。追加列を使う際も、
マニフェストの明示 map、対象 ID、関係固有の安全な evidence、Issue #7 の向きが揃う拡張として
別途承認する。

## provenance、更新、重複、失敗

Issue node と全 relation に独立した snake_case provenance を付け、Neo4j では既存どおり
camelCase のフラット属性にする。`source_id` は `exportId:issueId`（SourceDocument は exportId）、
`source_type` は `issue-trackers`、`source_locator` は `inputs/issue-trackers/<export-id>/original.*`、
`source_anchor` は CSV なら `row:<physical-row>`、JSON なら JSON Pointer、`source_revision` は
manifest の digest、時刻は manifest の UTC 値、`updated_by` は役割 ID とする。関係は node と
同じ原文を使っても、`source_anchor` を `row:<n>:assignee` 等の関係固有位置にする。

同じ `issueId` と同一正規化内容の再取込は `MERGE` され、node 数・relation key 数・原文 mtime
を変えない。内容差分は次のように扱う。

1. 同一 export 内で同じ `issueId` が二行/二要素なら `duplicate_record_id`。同じ行を結合しない。
2. 異なる export が同じ `issueId` を出し、正規化結果又は provenance が異なれば
   `conflicting_record`。最新日時・行順・ファイル名で後勝ちにしない。
3. 状態・期限・担当・要件を更新するには、既存の承認済み Issue の変更手順を別途人間承認し、
   `CHANGED_BY` 根拠を用意するまで拒否する。現行 `RELATION.key` に複数根拠を安全に保持する
   Assertion 拡張は未実装なので、根拠を黙って上書きしない。
4. 未知要件・未登録担当・存在しない関連端点は `unknown_target`。バッチ全体をロールバックする。

実装は検証エラーを値・原文・絶対パスを含まない `InputValidationError.as_dict()` 形式で集約し、
一件でもエラーなら Neo4j へ一件も書かない。追加コードは少なくとも次の code を使う:
`invalid_manifest`, `unsupported_encoding`, `invalid_csv`, `invalid_json`, `missing_column`,
`missing_required_value`, `invalid_date`, `invalid_status`, `duplicate_record_id`,
`unknown_target`, `conflicting_record`, `sensitive_content`, `pii_or_unsafe_assignee`。

## 有効・無効例

有効な CSV（ヘッダは manifest と完全一致）:

```csv
課題ID,件名,状態,期限,担当ID,関連要件ID
ISSUE-ORDER-001,注文取消期限を確認する,対応中,2026-10-01,TEAM-ORDER,REQ-ORDER-001
```

これは `Issue ISSUE-ORDER-001`、`issueStatus=in_progress`、`dueDate=2026-10-01`、
`ISSUE-ORDER-001|RELATES_TO|TEAM-ORDER`、`ISSUE-ORDER-001|RELATES_TO|REQ-ORDER-001`、
`ISSUE-ORDER-001|DERIVED_FROM|SRC-TRACKER-20260912` を生成する（担当 Entity は既登録が前提）。

有効な JSON は、`recordsPath: "$.issues"` を伴う次のような object である。

```json
{"issues":[{"key":"ISSUE-ORDER-001","summary":"注文取消期限を確認する","state":"対応中","due":"2026-10-01","owner":"TEAM-ORDER","requirements":"REQ-ORDER-001"}]}
```

無効例は、(a) `担当: 山田太郎` 又はメールアドレス、(b) `期限: 10/1`、(c) statusMap にない
`進行中`、(d) `関連要件ID: REQ-UNKNOWN`、(e) 同一課題 ID の二行、(f) UTF-16 CSV、(g) マニフェストに
ない/曖昧なヘッダ、である。いずれも安全なエラーだけを返し、部分取込も自動推測も行わない。

## Luna 向け実装・テスト指示

1. 最初に `AGENTS.md`、Issue #7/#8、本書、`domain_graph/ingest.py`、`neo4j_store.py`、既存 tests を読む。既存 `SOURCES`、ID、8語彙、`RELATION.key`、snake_case provenance、Node.js 接続境界を後方互換で維持する。
2. `issue-trackers` を専用 loader とし、通常の一件一ファイル JSON loader と混同しない。manifest を先に厳格検証し、CSV/JSON の header/key は明示 map だけで読む。原文 bytes と mtime を変更しない。
3. SourceDocument、Issue、既登録 Entity/Requirement への各 relation を一つの normalized batch として構築する。relation ごとの anchor/evidence/provenance を付け、全 endpoint を検査してから既存の単一トランザクションに渡す。
4. 期限・状態の正規化値を Issue node の許可プロパティとして Neo4j write に追加する場合、承認済み人間定義保護と同一内容再取込での非更新を保つ。更新競合を最新値で解決しない。
5. fixture は `tests/fixtures/issue-trackers/` 等に安全な架空 ID のみを置く。原文には氏名、メール、token、password、実在 URL query を置かない。テストでは valid CSV/JSON、UTF-8 BOM、LF/CRLF、引用符付き改行、空任意列、必須欠損、header/key 不一致、文字コード、日付、status、重複、未知端点、PII/secret、同一取込二回、差分競合、AI/承認分離、原文 hash/mtime 不変、Neo4j rollback を観測する。
6. 実行は最低 `python3 -m unittest discover -v`、Node/Neo4j 保存を変更した場合は `npm test`、可能なら `npm run test:neo4j` とする。変更ファイル、結果、未実装の Assertion/明示更新承認を PM に報告する。

## 受入観測

| 観測 | 合格条件 |
| --- | --- |
| 明示変換 | CSV header/JSON key と status map が manifest 完全一致で、推測列変換がない。 |
| グラフ対応 | Issue、SourceDocument、担当/要件、期限、状態と、`DERIVED_FROM`/`RELATES_TO` の向き・キー・provenance を確認できる。 |
| 欠損・形式 | 必須/任意列、空値、UTF-8/BOM/改行、日付、JSON path の有効/無効を安全な機械可読エラーで判定する。 |
| 安全性 | 原文・秘密・PII を graph/log/error に複写せず、原文 hash/mtime が不変である。 |
| 更新・冪等性 | 同一 batch は件数不変、曖昧な重複/異なる内容は失敗、未知端点は全体ロールバックする。 |
| 既存互換 | `python3 -m unittest discover -v` が通り、既存入力、承認/AI 分離、Neo4j の `id`/`RELATION.key` 契約を壊さない。 |
