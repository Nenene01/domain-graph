# Issue #6: GitHub Issue / PR / commit 連携設計

## 結論、対象範囲、調査根拠

GitHub API の取得結果を、安全なスナップショットとして `inputs/github/` に保存してから、既存の
検証・正規化・Neo4j 取込へ渡す二段階方式を採用する。これによりライブ API の再試行とグラフの
再取込を分離し、原文相当の取得結果、取得時刻、GitHub 識別子、更新元を追跡しつつ、token や
認証ヘッダを入力・グラフ・ログに残さない。同期は読み取り専用であり、GitHub 上の Issue/PR/
commit/review を変更、close、label 変更、コメント投稿しない。

対象はリポジトリ単位の Issue、PullRequest、Commit、PR review と、それらから明示的に得られる
関係の差分同期、ローカル入力契約、取込、監査、テストである。本文・コメント・コミットメッセージ
から要件や設計との関係を AI/正規表現で推測すること、GitHub webhook、GraphQL、他サービスとの
照合、ユーザー氏名/メール/本文の保存、#3 の不整合判定、#4 のMCP公開は対象外とする。

2026-09-12 に確認した GitHub Issue の実状態は、#1、#5、#7、#8、#9、#10、#11 が closed、#6 は
open、#2、#3、#4 は open である。実装履歴は `8d87a2f`（#9）、`5a4156a`（#5）、`f19f871`（#11）、
`6a4c931`（#8）、`dee7fb2`（#7）まで main にあり、GitHub PR は0件であった。従って本設計は
Issue #7 の `DomainNode` / `RELATION {key=from|type|to}`、#8/#11 の安全な入力と一括ロールバック、
#5 の manifest 型インポート、#9 の読み取り API を正本として、これらを変更せず拡張する。

## 不変条件と識別子

- `DomainNode.id` は全プロジェクトで一意、`RELATION.key` は `from|type|to` のままとする。GitHub の
  numeric database ID や SHA を単独キーに使わない。
- 同期対象リポジトリは、人間が作成する `repositoryId`（`^[A-Z0-9]+(?:-[A-Z0-9]+)+$`）で識別する。
  例は `GH-NENENE01-DOMAIN-GRAPH` で、owner/name と対応する。owner/name の自動 slug 化は衝突を
  招くため禁止する。
- ノード ID は `GH-...-ISSUE-<number>`、`GH-...-PR-<number>`、
  `GH-...-COMMIT-<SHAの大文字16進表記>`、`GH-...-REVIEW-<GitHub review database id>` とする。
  元の番号、完全な小文字 SHA、GitHub node/database ID は各ノードの許可済みスカラー属性
  `githubNumber`、`githubSha`、`githubDatabaseId` に保持する。URL は provenance の
  `source_locator` に認証情報・query/fragment なしで保存する。
- すべて `extractionMethod=deterministic_import` とし、同期直後は `approvalStatus=proposed`、
  `confidence=1.0` とする。GitHub の状態は外部観測事実だが、Domain Graph 内の業務・設計の
  承認済み定義ではないためである。人間が snapshot 単位で承認した場合のみ manifest の
  `approvalStatus=approved` を許す。AI が補う候補は別入力・`ai_inferred/proposed` とし、
  この同期結果や人間承認値を置換できない。
- `sourceType=github`、`updatedBy=github-api`（又は承認者の安全な役割 ID）、`retrievedAt` は取得完了
  時刻、`observedAt` は GitHub の `updated_at` / `submitted_at` / `committedDate`、`sourceRevision`
  は snapshot の SHA-256 とする。各ノード・各関係は独立した `source_anchor` を持つ。

## 入力と同期境界

### ディレクトリと manifest

同期クライアントは認証済み HTTPS API を呼び、応答をそのまま保存せず、許可フィールドだけを
検証・縮約した JSON snapshot を作る。原文本文・コメント本文・review 本文・コミット本文、
`author`/`committer` の氏名・メール、token、HTTP header、rate-limit header は保存しない。
タイトル、URL、ID、状態、時刻も `_safe_excerpt` と同等の機密/メール検査および長さ上限を通す。

```text
inputs/github/
  GH-NENENE01-DOMAIN-GRAPH/
    SYNC-20260912T010203Z-000001/
      manifest.json
      snapshot.json
```

`SYNC-*` は単なる取得実行IDであり、グラフ node ID にはしない。`snapshot.json` と `manifest.json` は
UTF-8・単一 object・BOM/NUL/重複キーなし、書込みは temp file + fsync + rename として中途半端な
snapshot を読ませない。既存 snapshot は絶対に上書き・削除せず、同一 `sourceRevision` は再利用する。
同期 cursor は repo 外の実行状態（例: CI artifact / secret manager が管理する state store）に保存し、
GitHub token とは別の秘密として扱う。cursor の耐久保存先・保持期間は未登録のため、初回実装では
manifest に cursor を含めず、明示 CLI 引数又は外部 state interface からだけ渡す。

`manifest.json` の必須契約は次の通りである。`repositoryUrl` は canonical な
`https://github.com/<owner>/<repo>` のみで、query/fragment/userinfo を禁止する。

```json
{
  "schemaVersion": "1.0",
  "syncId": "SYNC-20260912T010203Z-000001",
  "repositoryId": "GH-NENENE01-DOMAIN-GRAPH",
  "repositoryUrl": "https://github.com/Nenene01/domain-graph",
  "snapshotFile": "snapshot.json",
  "sourceRevision": "sha256:<64 lowercase hex>",
  "retrievedAt": "2026-09-12T01:02:03Z",
  "updatedBy": "github-api",
  "approvalStatus": "proposed",
  "syncMode": "incremental",
  "window": {"updatedSince": "2026-09-11T00:00:00Z", "updatedUntil": "2026-09-12T01:02:03Z"}
}
```

`snapshot.json` は API payload のコピーではなく、次の allowlist schema に限定する。値を欠損時に
推測せず、Issue/PR/commit の ID と URL、必要時刻が欠ければ当該 snapshot 全体を拒否する。

```json
{
  "issues": [{"number": 6, "databaseId": "...", "url": "https://github.com/.../issues/6",
    "title": "...", "state": "OPEN", "createdAt": "...Z", "updatedAt": "...Z",
    "closedAt": null, "labels": ["safe-label"]}],
  "pullRequests": [{"number": 12, "databaseId": "...", "url": "https://github.com/.../pull/12",
    "title": "...", "state": "OPEN", "createdAt": "...Z", "updatedAt": "...Z",
    "mergedAt": null, "closedAt": null, "headSha": "<40 hex>", "mergeCommitSha": null,
    "linkedIssueNumbers": [6]}],
  "commits": [{"sha": "<40 hex>", "url": "https://github.com/.../commit/<sha>",
    "committedAt": "...Z", "parentShas": ["<40 hex>"], "pullRequestNumbers": [12]}],
  "reviews": [{"databaseId": "...", "pullRequestNumber": 12, "url": "https://github.com/.../pull/12#pullrequestreview-...",
    "state": "APPROVED", "submittedAt": "...Z", "commitSha": "<40 hex>"}]
}
```

PR が Issue を closing keyword で参照するなどの関連は API が構造化して返す `linkedIssueNumbers`
だけを使用する。本文、タイトル、ブランチ名、commit message を解析して `RELATES_TO`、要件、設計を
作らない。GitHub の REST/GraphQL のどちらを選ぶかは未登録であり、実装は `GitHubClient` protocol
（ページング、ETag/updated-since、rate-limit response、上記 snapshot DTO のみを返す）に閉じ込める。
初回は REST API の GET のみを採用してよいが、GraphQL 専用フィールドを契約に混ぜない。

## グラフ対応と関係の向き

まず同期 manifest 自体を `SourceDocument` として `GH-...-SYNC-...` に正規化する。これは取得
batch の追跡専用であり、snapshot 全文を property に保存しない。各 GitHub ノードはこの SourceDocument
へ `DERIVED_FROM` を持つため、#9 API は source locator と取得時刻を通常どおり返せる。

| 元情報 | ノード | 必須の関係（保存向き） | 追加属性 |
| --- | --- | --- | --- |
| Issue | `Issue` | `Issue → DERIVED_FROM → SourceDocument` | `githubNumber`, `githubState`, `githubCreatedAt`, `githubUpdatedAt`, `githubClosedAt` |
| PR | `PullRequest` | `PullRequest → DERIVED_FROM → SourceDocument`、PR が Issue を明示関連付けした時 `PullRequest → RELATES_TO → Issue` | `githubNumber`, `githubState`, `githubMergedAt`, `githubClosedAt`, `headSha`, `mergeCommitSha` |
| Commit | `Commit` | `Commit → DERIVED_FROM → SourceDocument`、API が PR 所属を返した時 `Commit → RELATES_TO → PullRequest`、親 SHA が snapshot に在る時 `Commit → DERIVED_FROM → parent Commit` | `githubSha`, `githubCommittedAt` |
| Review | `SourceDocument` ではなく `DomainNode {type: SourceDocument}` の review 記録 | `Review → VERIFIES → PullRequest`、`Review → DERIVED_FROM → SourceDocument` | `githubDatabaseId`, `reviewState`, `githubSubmittedAt`, `commitSha` |

Review は Issue #7 の node type に `Review` がないため、専用型を勝手に追加せず、暫定的に
`SourceDocument`（title は固定の「GitHub PR review」）とする。表中の Review は論理名である。
`CHANGED_BY` は「変更対象 → 変更を起こした PR/commit」という #7 の向きを守るため、PR に含まれる
commit を表すのには用いない。commit→PR の所属は「commit が PR により変更された」という事実では
ないため、`Commit → RELATES_TO → PullRequest` として保存する。

最終的な確定語彙は以下である。

- `Issue/PullRequest/Commit/review → DERIVED_FROM → SourceDocument`: snapshot のどの item かを
  `source_anchor`（例 `$.pullRequests[0]`）で示す。
- `PullRequest → RELATES_TO → Issue`: GitHub API が構造化して返す issue link のみ。anchor は
  `$.pullRequests[0].linkedIssueNumbers[0]`。
- `Commit → RELATES_TO → PullRequest`: API がそのコミットの PR 所属を構造化して返す場合のみ。
- `review(SourceDocument) → VERIFIES → PullRequest`: review state が `APPROVED`、`CHANGES_REQUESTED`、
  `COMMENTED`、`DISMISSED` のいずれでも記録する。`VERIFIES` は「レビューという検証行為」であり、
  `APPROVED` を要件/実装の正しさの承認と解釈しない。
- `Commit → DERIVED_FROM → parent Commit`: 親 SHA が同一 snapshot に存在する場合だけ。親が未取得なら
  relation を作らず、存在しない端点を作らない。

同一 `from|type|to` に、異なる snapshot から異なる provenance/evidence が来た場合、現行一関係一根拠
モデルでは更新や追加根拠を安全に保持できない。内容一致は no-op、差分は `conflicting_record` として
batch 全体をロールバックし、監査に snapshot digest と安全な ID だけを残す。履歴や最新状態を
「後勝ち」にしたい場合は Assertion/versioned assertion モデルと承認手順の決定が必要であり、本 Issue
で黙って実装しない。

## 差分同期、削除、失敗、監査

1. 同期クライアントは repository allowlist と短期・read-only GitHub credential を受け、ETag 又は
   `updated_since` を用いてページングする。`429`/`403 rate limit` は `Retry-After` を尊重して
   `rate_limited` として停止し、部分 snapshot を publish しない。ネットワーク/5xx は指数 backoff の
   対象だが、同じ `syncId` の結果を上書きしない。
2. snapshot writer は全ページを検証して digest を計算後、manifest と共に原子的に publish する。
   `sourceRevision` が同じ既存 snapshot は再生成せず成功扱いにできる。
3. loader は manifest→digest→snapshot schema→全 ID/URL/日時/参照端点の順に検証し、全 records を
   作ってから `normalize_inputs` と既存 `Neo4jStore` の単一 write transaction に渡す。一件でも
   `invalid_manifest`、`invalid_github_snapshot`、`unknown_target`、`conflicting_record`、
   `sensitive_content` なら永続化ゼロである。
4. GitHub の closed/merged は node の状態属性であり削除ではない。API の検索窓に無かったことは
   削除の根拠にしない。deleted Issue/PR/commit、force-push、PR の関連解除を検知してグラフを削除・
   relation 削除する仕様は未登録であり、初回は tombstone も物理削除も行わない。安全な削除/履歴を
   要するなら Assertion 拡張と保持期間を先に決める。
5. 監査イベント `github_sync` を成功/差分なし/検証失敗/API失敗の全てで一件出す。許可属性は
   `occurredAt`, `requestId`, `repositoryId`, `syncId`, `sourceRevision`, `syncMode`, `outcome`,
   `errorCode`, 件数、duration、HTTP status のみ。token、Authorization、URL query/fragment、本文、
   title、actor、exception text、絶対パス、メールは監査/ログに出さない。#9 の `graph_trace` 監査契約は
   変更せず、取り込まれたノード/関係は同 API の既定 `includeProposed=false` では返らない。

## #9 問い合わせAPIとの整合

`query_contract.py` の ID pattern と8語彙は拡張しない。同期直後のデータを確認するには、認可済み主体が
`includeProposed=true` と `domain-graph:read-proposed` scope を明示し、開始 ID を例えば
`GH-NENENE01-DOMAIN-GRAPH-PR-12` として trace する。応答は保存向きの `RELATES_TO`、`VERIFIES`、
`DERIVED_FROM`、各 relation の camelCase provenance、`evidenceLevel=proposed_or_inferred` を返す。

snapshot を人間承認した場合に限り、`approved` の GitHub ノード/関係は既定 trace に含まれる。ただし
GitHub の review state や merge state から Requirement/Design/Code が満たされたと結論付けない。
PR/commit と要件の追跡を作るには、人間承認済みの別入力で `RELATES_TO`/`IMPLEMENTS` 等を明示するか、
将来の候補 Assertion を `ai_inferred/proposed` として追加する必要がある。

## 実装配置とLunaへの具体的指示

1. `docs/design/issue-6-github-sync.md`、`AGENTS.md`、Issue #5/#7/#8/#9、
   `domain_graph/ingest.py`、`neo4j_store.py`、`query_repository.py` を先に読む。Node.js の接続層を
   GitHub 取込で重複利用/置換せず、Python の既存 public loader と `RELATION.key` を壊さない。
2. 新規 `domain_graph/github_sync.py` に HTTP 非依存の `GitHubClient` protocol、allowlisted snapshot
   DTO、retry classification、atomic snapshot publisher を置く。token は引数の credential provider が
   リクエストにだけ注入し、DTO・例外・logger・manifest へ渡さない。実 HTTP client/credential provider
   は未登録のため、標準 libraryに固定せず注入可能にする。
3. 新規 `domain_graph/github_import.py` に `load_github_snapshot(sync_dir)` を置き、#5 と同じ
   manifest-first、strict JSON、digest、safe-path、全件検証の方式で records を返す。`SOURCES` へ単に
   `github` を足して通常 JSON loader に混ぜない。必要なら `load_github_snapshots(root)` を追加する。
4. `domain_graph/ingest.py` は最小限に、GitHub固有の安全 validator・Node追加属性を `normalize_inputs`
   と `neo4j_statements` が落とさないよう allowlist で拡張する。現状は Issue の `issueStatus/dueDate`
   以外を捨てるため、`github*`, `headSha`, `mergeCommitSha`, `reviewState`, `commitSha` を明示的に
   projection/parameter化する。任意 map や本文を Neo4j へ流さない。
5. 現在の Neo4j edge `SET` は同じ key の provenance を上書きするため、GitHub含む既存 edge との
   内容差分を先に検出して拒否する。node の approved/proposed 保護だけに依存しない。修正は
   InMemoryGraph と Neo4j の両方で同じ conflict/no-op 方針にし、トランザクション外で部分更新しない。
6. `domain_graph/audit.py` の protocol を再利用して `github_sync` を許可フィールドだけで記録する。
   #9 の API response・`graph_trace` schema・read Cypherは変更しない。テスト fixture は架空ID、
   `example.invalid` 以外のURL、個人名、メール、token、commit本文を含めない。

変更候補は `docs/design/issue-6-github-sync.md`、`domain_graph/github_sync.py`、
`domain_graph/github_import.py`、`domain_graph/ingest.py`、`domain_graph/audit.py`、
`tests/test_github_import.py`、`tests/test_github_sync.py`、必要なら Neo4j integration test である。
`query_contract.py`、`query_service.py`、`query_repository.py`、`api.py` は互換確認のテスト追加以外は
変更しない。

## 受入テスト

| 観測 | 合格条件 |
| --- | --- |
| 正規化 | Issue/PR/commit/review と SourceDocument のID、type、保存向き、`from|type|to`、各 provenance を fixture で確認できる。 |
| 出所 | URL、GitHub ID/SHA、digest、取得/観測時刻、更新元、anchor を node と relation の双方から得られる。 |
| 差分・再実行 | 同一 snapshot を2回処理して node/edge 数・createdAt が変わらない。digest 同一は no-op、同一ID/keyの異内容は `conflicting_record`、未知端点は全体 rollback。 |
| 安全性 | token/header、メール、本文、commit message、query付きURL、絶対パスが input/graph/error/audit/log に無く、safe error だけを返す。 |
| API整合 | proposed snapshot は既定 trace に現れず、`read-proposed` scope + `includeProposed` で provenance と保存向きの関係を返す。 |
| 失敗 | 401/403、429、5xx、ページ途中失敗、digest不一致、NUL/重複JSON key、URL/時刻/SHA不正、rate limit を再現し、部分snapshot・部分永続化が無い。 |
| 互換 | `python3 -m unittest discover -v`、`npm test`、`npm run lint`、`npm run build`、可能なら `RUN_NEO4J_TESTS=true npm run test:neo4j` が通る。 |

## 未登録仕様・実装前の決定待ち

1. GitHub REST と GraphQL のどちらを正式採用するか、GitHub App / fine-grained PAT の発行・保管・
   repository allowlist・read scope・期限・rotation を決める必要がある。実装は credential 値を
   repository/config/graph に保存しない。
2. `updatedSince` cursor の永続先、初回バックフィル期間、rate-limit待機のCI運用、snapshot/監査の
   保持期間とアクセス制御は未登録である。
3. commit と PR の所属を `RELATES_TO` とする暫定判断、review 専用 node type が無いこと、同一 edge
   への複数根拠/状態履歴、force-push・関連解除・削除の扱いには Assertion/versioning の設計決定が
   必要である。決定までは削除・後勝ち・自動統合を実装しない。
