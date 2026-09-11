# Issue #9: 根拠付きグラフ問い合わせ API 設計

## 決定、目的、対象外

GitHub Issue #9 の実際の題名は「根拠付きグラフ問い合わせAPIを実装する」である。本設計は、
取り込み済みの Domain Graph から、開始ノードと任意の到達先の間にある**承認済みで根拠を
持つ**経路を、ノード・保存済み関係の向き・関係ごとの根拠とともに読み取る API を定義する。
これにより、要件から実装、出所、影響範囲、未接続要素を確認するという Issue 本文の問いに、
推測を混ぜず答えられるようにする。

対象は Python の読み取り専用 API 境界、Neo4j 読み取りリポジトリ、認証済み主体の認可、
安全な応答・監査、およびそのテストである。取り込み形式・スキーマ・データの変更、書込み API、
AI による補完、Assertion モデルの追加、GitHub のライブ取得、認証プロバイダの選定・トークン
発行は対象外とする。Node.js 側は現状 `src/config.ts` と `src/neo4j.ts` の接続基盤だけであり、
本 Issue で Python 取込を Node.js に重複実装しない。

## 根拠と既存契約

| 根拠 | API への反映 |
| --- | --- |
| `AGENTS.md` | 原文へ辿れる出所、取得日時、識別子、更新元を返す。未登録・根拠不足は推測せず明示し、最小権限・短期トークン・監査を前提とする。 |
| `README.md` MVP 節 | `REQ-ORDER-001` から `DEC-ORDER-001`、`MTG-2026-001`、`DSN-ORDER-001`、`CODE-ORDER-001` を辿り、`not_registered` と `evidence_insufficient` を区別する。 |
| `PLAN.md` Phase 1 / 共通受入条件 | 根拠付きの再現可能な検索を優先し、AI 推論を事実とせず、秘密情報を返却・記録しない。 |
| Issue #7 成果物 | `(:DomainNode {id})-[:RELATION {key, type}]->(:DomainNode)`、`key=from|type|to`、8語彙、関係ごとの provenance、承認済み経路だけを既定にする。 |
| Issue #8 / #11 の成果物と `domain_graph/ingest.py` | 入力・メモリでは snake_case provenance、Neo4j では camelCase のフラット属性を使う。安全なエラー、AI/承認の分離、端点検査・冪等取込を変更しない。 |
| Issue #5 成果物 | Issue / SourceDocument を含む Phase 2 データでも、`RELATES_TO` と `DERIVED_FROM`、安全な locator と provenance を同じ契約で返す。 |
| `domain_graph/query.py` / `InMemoryGraph.trace` | 現行の双方向探索と absence status は後方互換のローカル・テスト用契約として残す。API はこれを HTTP 応答へ直接露出せず、ページング可能で決定的な結果へ整形する。 |

## 不変条件とグラフ境界

- 読み取り対象は `:DomainNode` と `:RELATION` のみである。ノード ID は不変・グローバル一意、
  関係キーは不変の `from|type|to` とする。API は作成・更新・削除の Cypher を発行しない。
- 保存された向きは絶対に反転しない。探索では `direction=both` を指定できるが、返す relation の
  `fromId` と `toId` は Neo4j に保存された向きのままとする。
- 使用可能な `relationTypes` は `DEFINED_BY`、`RELATES_TO`、`IMPLEMENTS`、`VERIFIES`、
  `BLOCKS`、`CHANGED_BY`、`DECIDED_IN`、`DERIVED_FROM` のみである。各語彙の意味・向きは
  Issue #7 の表を正本とし、この API で別名・任意 Cypher・推論関係を受け付けない。
- 既定の根拠条件は、経路上の**全 relation** が `approvalStatus="approved"`、
  `extractionMethod != "ai_inferred"`、`evidenceExcerpt` が空でないこととする。端点ノードも
  同じ承認条件を満たすものだけを `nodes` に含める。`proposed`、`rejected`、`superseded`、
  `ai_inferred` は既定結果に含めない。
- AI 推論候補を見る用途は `includeProposed=true` を明示した場合だけ許可する。その場合にも
  `approvalStatus`、`extractionMethod`、`confidence` をそのまま返し、`evidenceLevel` を
  `proposed_or_inferred` とする。承認済みの `evidenceLevel="approved"` と混在させて一つの
  事実・到達経路として返してはならない。
- Phase 1 の一関係一根拠モデルでは、複数根拠の競合を統合しない。該当 relation が承認済みの
  根拠として一意に読めないなら `evidence_insufficient` として返し、Assertion 拡張を推測して
  実装しない。

## 実装配置と責務

Luna は次の責務分離で実装する。既存の `domain_graph/ingest.py`、`neo4j_store.py`、
`query.py` の公開振る舞いと既存テストを変更しない。

| 配置 | 責務 |
| --- | --- |
| `domain_graph/query_contract.py`（新規） | request/response DTO、列挙値、上限、`QueryApiError`、安全な入力検証・ページ token 署名検証。HTTP ライブラリや Neo4j driver を参照しない。 |
| `domain_graph/query_repository.py`（新規） | パラメータ化した固定 Cypher のみを実行し、Neo4j record を DTO 用の camelCase 値へ変換する。書込み・任意 Cypher を持たない。 |
| `domain_graph/query_service.py`（新規） | 認可、既定の承認フィルタ、開始 ID 存在判定、探索、ページング、status 決定、監査イベントを組み立てる。テストでは InMemoryGraph 相当の read repository を注入可能にする。 |
| `domain_graph/api.py`（新規） | HTTP adapter。`POST /v1/graph/traces` だけを公開し、JSON、content type、request ID、Principal を service に渡して安全な JSON を返す。 |
| `domain_graph/audit.py`（新規） | 構造化監査 sink の protocol と許可済みフィールドのイベントを提供する。監査保存先の実装は環境で注入する。 |

HTTP サーバー（WSGI/ASGI、プロセス管理、TLS 終端）はこのリポジトリに未登録のため、任意の
フレームワークを追加して決めない。`api.py` はフレームワークに依存しない request/response
adapter、又は採用済みサーバーが呼べる最小 callable として実装する。デプロイ時の TLS、rate limit、
認証検証は API より前段の信頼済み gateway/middleware が担う。

## HTTP 契約

### エンドポイント

`POST /v1/graph/traces`。`Content-Type: application/json` を必須とし、リクエスト・レスポンスは
UTF-8 JSON object のみ、本文最大 16 KiB とする。GET、任意のパス、JSON 以外、配列トップレベル、
未知フィールドは受け付けない。読み取り API なので成功時は `Cache-Control: no-store` を返す。

```
Authorization: Bearer <short-lived-token>     # gateway が検証し API には渡さない
X-Request-Id: <任意の安全な相関 ID>
```

API adapter は外部 header を認証事実として信用しない。gateway/middleware が署名・期限・issuer・
audience を検証し、次の `Principal` を process 内の信頼済みコンテキストとして渡す。Principal が
無い、期限切れ、検証不能の場合は service を呼ばず `401 unauthorized` とする。認証プロバイダ、
JWKS URL、claim 名、トークン形式は未登録のため本 Issue では固定しない。実装で header を直接
復号したり、開発用固定 token をリポジトリへ置いたりしてはならない。

```python
@dataclass(frozen=True)
class Principal:
    subject_id: str          # 秘密値・メールアドレスを含まない opaque ID
    project_ids: frozenset[str]
    scopes: frozenset[str]  # "domain-graph:read" が必須
    token_expires_at: datetime
```

リポジトリに project/tenant 属性はまだ定義されていない。そのため Phase 1 の実装は
`domain-graph:read` scope を必須にし、project 分離を**有効化しない**（project を request で
受け取らない）。project/tenant 属性とデータ移行、認可規則が別 Issue で確定するまでは、
project claim による Cypher 条件を推測して追加しない。scope 不足は `403 forbidden`、Principal 内の
期限切れは `401 unauthorized` とする。

### request body

```json
{
  "startId": "REQ-ORDER-001",
  "targetId": "CODE-ORDER-001",
  "direction": "both",
  "relationTypes": ["IMPLEMENTS", "DECIDED_IN", "DERIVED_FROM"],
  "maxDepth": 6,
  "pageSize": 25,
  "pageToken": "opaque-signed-token",
  "includeProposed": false
}
```

| 項目 | 必須 | 規則 |
| --- | --- | --- |
| `startId` | 必須 | `^[A-Z0-9]+(?:-[A-Z0-9]+)+$`、最大 128 文字。グラフ上の存在は service が確認する。 |
| `targetId` | 任意 | `startId` と同じ形式。指定時は到達可否を返す。未指定時は開始点の周辺を返す。 |
| `direction` | 任意 | `outbound` / `inbound` / `both`。既定 `both`。これは探索方向だけで、relation の保存向きは変えない。 |
| `relationTypes` | 任意 | 重複のない上記8語彙の subset。未指定は全8語彙。空配列は `invalid_request`。 |
| `maxDepth` | 任意 | 整数 1〜6、既定 6。ページ token 使用時も同じ条件でなければならない。 |
| `pageSize` | 任意 | 整数 1〜100、既定 25。1 ページに返す relation 数の上限。 |
| `pageToken` | 任意 | 前ページから返る不透明・署名済み token のみ。任意の offset、Cypher、ID を復号して信用しない。 |
| `includeProposed` | 任意 | boolean、既定 `false`。`true` は `domain-graph:read-proposed` scope を追加で必須とする。 |

token には canonicalized request（`startId`、`targetId`、direction、relationTypes、maxDepth、
includeProposed）、最後の安定した順序キー、発行時刻・有効期限を含め、HMAC 鍵は外部 secret manager
からプロセスへ注入する。token は 10 分で失効し、別 Principal、別リクエスト条件、署名不正、期限切れ
なら `400 invalid_page_token` とする。鍵や token payload をエラー・ログに出してはならない。

### 成功 response

```json
{
  "status": "ok",
  "query": {
    "startId": "REQ-ORDER-001",
    "targetId": "CODE-ORDER-001",
    "direction": "both",
    "maxDepth": 6,
    "includeProposed": false
  },
  "nodes": [{
    "id": "REQ-ORDER-001",
    "type": "Requirement",
    "title": "注文を登録できる",
    "approvalStatus": "approved",
    "extractionMethod": "human_curated",
    "confidence": 1.0,
    "provenance": {
      "sourceId": "REQ-ORDER-001",
      "sourceType": "requirements",
      "sourceLocator": "inputs/requirements/REQ-ORDER-001.json",
      "sourceAnchor": "$",
      "sourceRevision": "2026-01-15",
      "retrievedAt": "2026-01-15T00:00:00Z",
      "updatedBy": "product-owner",
      "observedAt": "2026-01-15T00:00:00Z",
      "evidenceExcerpt": "注文を登録できる"
    }
  }],
  "relations": [{
    "key": "CODE-ORDER-001|IMPLEMENTS|DSN-ORDER-001",
    "type": "IMPLEMENTS",
    "fromId": "CODE-ORDER-001",
    "toId": "DSN-ORDER-001",
    "traversedDirection": "reverse",
    "evidenceLevel": "approved",
    "approvalStatus": "approved",
    "extractionMethod": "human_curated",
    "confidence": 1.0,
    "provenance": {
      "sourceId": "CODE-ORDER-001",
      "sourceType": "source-metadata",
      "sourceLocator": "inputs/source-metadata/CODE-ORDER-001.json",
      "sourceAnchor": "$.relations[0]",
      "sourceRevision": null,
      "retrievedAt": "2026-01-15T00:00:00Z",
      "updatedBy": "tech-lead",
      "observedAt": "2026-01-15T00:00:00Z",
      "evidenceExcerpt": "注文登録設計を実装する"
    }
  }],
  "page": {"pageSize": 25, "nextPageToken": null, "truncated": false},
  "meta": {"requestId": "client-or-generated-id"}
}
```

返却値は API 用に camelCase とする。これは入力/メモリ上の snake_case provenance map を変更する
ものではない。`provenance` は node と relation のそれぞれに独立して返し、端点ノードの provenance
で relation の根拠を代用しない。`sourceRevision` は未登録なら `null`、必須の source ID、locator、
anchor、取得日時 (`retrievedAt`)、更新元 (`updatedBy`)、観測日時 (`observedAt`) は省略しない。
`evidenceExcerpt` は取り込み時に安全化された短い値だけを返し、原文全文を読む API にはしない。

relation は `(key ASC, fromId ASC, toId ASC)` で安定ソートし、同一ページに必要な端点 node を
`id ASC` で重複なく返す。`targetId` がある `ok` では、target に到達する relation だけを返す。
複数経路がある場合もこの順序で上限まで返し、ページをまたぐ。ページ全体で探索結果が 10,000
relation を超える場合は DB を継続走査せず、`413 query_limit_exceeded` とする（pageSize を小さくしても
回避不可）。DB 実行は 2 秒、service 全体は 3 秒で打ち切り、`504 query_timeout` を返す。

### absence とエラー response

`not_registered` と `evidence_insufficient` は問い合わせとして正常に完了した状態なので HTTP 200
で返す。前者は `startId` が登録されていない場合だけであり、target が未登録・到達不能・承認済み
根拠が無い・proposed/AI 経路だけの場合は後者とする。いずれも `nodes=[]`、`relations=[]`、
`page={"pageSize":...,"nextPageToken":null,"truncated":false}` とし、存在しない target や除外された
relation の詳細を列挙しない。

```json
{
  "status": "evidence_insufficient",
  "query": {"startId": "REQ-ORDER-001", "targetId": "CODE-ORDER-001", "direction": "both", "maxDepth": 6, "includeProposed": false},
  "nodes": [], "relations": [],
  "page": {"pageSize": 25, "nextPageToken": null, "truncated": false},
  "meta": {"requestId": "client-or-generated-id"}
}
```

非成功は下記の固定・安全な形にする。入力値、Cypher、Neo4j URI、stack trace、認証 token、
evidence excerpt、絶対パス、メールアドレスを `message` に入れない。

```json
{"error":{"code":"invalid_request","message":"request is invalid"},"meta":{"requestId":"..."}}
```

| HTTP | code | 条件 |
| --- | --- | --- |
| 400 | `invalid_request` | JSON、フィールド型・列挙・範囲、未知 field、本文上限違反。 |
| 400 | `invalid_page_token` | token の署名、期限、Principal、要求条件が一致しない。 |
| 401 | `unauthorized` | Principal 不在、認証失敗、期限切れ。 |
| 403 | `forbidden` | `domain-graph:read` 又は proposed 用 scope がない。 |
| 413 | `query_limit_exceeded` | maxDepth/pageSize/探索 relation 総数の上限超過。 |
| 415 | `unsupported_media_type` | JSON 以外の content type。 |
| 429 | `rate_limited` | gateway の subject 単位制限に達した。`Retry-After` は gateway が付与する。 |
| 500 | `internal_error` | 予期しない内部失敗。詳細は内部の request ID と監査イベントでのみ照合する。 |
| 503 | `service_unavailable` | Neo4j 接続を確立できない、又は read repository が利用不能。 |
| 504 | `query_timeout` | 設定済みの DB/service deadline 超過。 |

## 固定 Cypher と injection 防止

query repository は API から Cypher 文字列、ラベル、property 名、ORDER BY、relationship type を
受け取らない。Cypher テンプレートはソースコード内の固定文字列一つにし、値はすべて driver の
パラメータ（例: `$startId`, `$targetId`, `$relationTypes`, `$maxDepth`, `$limit`）で渡す。
`maxDepth` は Cypher の可変長パターンへ直接補間せず、固定の `*1..6` で取得してから
`length(path) <= $maxDepth` で絞る。relation type も `r.type IN $relationTypes` の値比較にする。

探索は read transaction で次を満たす固定クエリ群とする。

1. `MATCH (start:DomainNode {id:$startId}) RETURN start.id` で開始 ID だけを判定する。
2. 開始がある場合だけ、固定 `MATCH p=(start)-[:RELATION*1..6]-(candidate)` を用いる。
   `direction` は固定された `outbound`/`inbound`/`both` の3テンプレートから adapter が選ぶ。
3. `ALL(r IN relationships(p) WHERE r.type IN $relationTypes AND ...承認条件...)` と、全 node の
   承認条件で絞る。`includeProposed=true` だけが別の固定条件を選択する。
4. target 指定時は `$targetId` との `candidate.id` 比較をパラメータで行い、path 内の
   relation を `UNWIND` して `r.key` 単位に distinct・安定順序・`$limit + 1` を取得する。
5. repository はノード/関係の許可リストだけを projection し、Neo4j record の map や任意 property
   を丸ごと返さない。

page token の cursor も `$afterKey` 等のパラメータとして使い、`SKIP` のクライアント指定を
禁止する。driver の read timeout と transaction metadata に request ID を設定する。接続 URI と
credential は既存 Node/Python の外部環境設定のままで、例外・監査・response に含めない。

## 認可、監査、機密情報

- gateway は TLS を必須にし、短期 Bearer token を検証する。API process には token 文字列を渡さず、
  `Principal` だけを渡す。read scope は書込み・管理 scope を含意しない。
- 監査は**成功、absence、4xx、5xx の全て**に一件記録する。必須属性は `eventType=graph_trace`,
  `occurredAt` (UTC), `requestId`, `principalSubjectId`, `outcome`, `httpStatus`, `startId`,
  `targetId` の有無、`direction`, `relationTypes`, `maxDepth`, `pageSize`, `includeProposed`,
  `returnedNodeCount`, `returnedRelationCount`, `durationMs`, `errorCode` の有無である。
- `principalSubjectId`、start/target ID は opaque ID として許可するが、Authorization header、token、
  cookie、password、Neo4j credential、原文、`evidenceExcerpt`、source locator の query/fragment、
  メールアドレス、IP アドレスを監査へ記録しない。`X-Request-Id` は制御文字なし・128文字以下の
  安全値だけを採用し、それ以外は新しい UUID を生成する。
- audit sink の失敗は問い合わせ結果を成功に見せかけない。fail-closed の運用を選ぶ場合は
  `503 service_unavailable`、非同期で耐久キューへ退避する運用を選ぶ場合は enqueue 成功を確認して
  から返す。どちらを採用するかは監査基盤が未登録なので、実装前に運用担当の決定を要する。
- `sourceLocator` がすでに入力検証を通った安全な相対パス/認証情報なし URL でも、API はリンク先を
  fetch せず、その文字列だけを返す。これにより SSRF と原文・秘密値の再露出を防ぐ。

## Luna 向け実装順序

1. 本書、`AGENTS.md`、`README.md`、`PLAN.md`、Issue #5/#7/#8 の設計書、Issue #11 実装、
   `domain_graph/query.py`、`ingest.py`、`neo4j_store.py`、既存 test を読み、既存の Python/Node
   接続と取込 API を変更しない。
2. query contract と service/repository の unit test を先に追加する。DTO の public response は
   camelCase、InMemoryGraph/入力は snake_case のままとし、明示的な変換関数で境界を作る。
3. InMemory read repository で `REQ→DEC/MTG←DSN←CODE`、8 relation 語彙、保存向き、approved/
   proposed/AI 分離、absence、ページ順序を通す。`query.trace()` の既存結果の順序に依存しない。
4. Neo4j repository を固定・パラメータ化 Cypher で追加し、`Neo4jStore` の書込みクエリや制約を
   変更しない。Neo4j driver の session は read transaction を使い、deadline と上限を必ず設定する。
5. HTTP adapter に Principal 注入 interface、content type/body size、request ID、固定安全エラーを
   実装する。認証検証済み Principal の供給元がまだ無いローカル実行では endpoint を公開しない。
6. audit sink を注入し、許可フィールドだけをテストで検査する。ログは error code/request ID のみを
   許容し、例外文字列をそのまま出力しない。
7. `python3 -m unittest discover -v`、`npm test`、`npm run lint`、`npm run build` を実行する。
   Neo4j を起動できる環境では `RUN_NEO4J_TESTS=true npm run test:neo4j` と API の integration test
   も実行する。既存テストの更新は互換を保つ追加だけにする。

## テストと受入観測

| 観測 | 自動テスト / 合格条件 |
| --- | --- |
| 代表追跡 | `REQ-ORDER-001` と `CODE-ORDER-001` で `ok`。REQ/DEC/MTG/DSN/CODE と relation ごとの独立 provenance を返す。 |
| 8語彙・向き | 全8 type を filter でき、`direction=both` で逆に辿っても `fromId/toId/key` は保存値のまま。 |
| provenance 変換 | 入力/メモリの snake_case を変更せず、API は source ID、locator、anchor、revision、取得日時、更新元、観測日時、根拠、confidence を camelCase で返す。 |
| 承認・AI 分離 | proposed/rejected/superseded/ai_inferred のみの経路は既定で `evidence_insufficient`。scope 付き `includeProposed=true` は `proposed_or_inferred` と明示してのみ返す。 |
| absence | 開始 ID 不在は HTTP 200 `not_registered`、target 不在・未到達・未承認だけは HTTP 200 `evidence_insufficient`。推測説明を返さない。 |
| ページング・上限 | 安定順序、改ざん/期限切れ/条件不一致 token の拒否、pageSize 100超過、depth 6超過、10,000 relation 超過、timeout を再現する。 |
| injection | ID、relation type、page token に quote、comment、Cypher 断片を含む値を渡しても、固定 query + parameter 以外が実行されず `invalid_request`/`invalid_page_token` になる。 |
| 認証認可 | Principal 無し/期限切れは 401、read scope 無しは 403、proposed scope 無しで `includeProposed=true` は 403。生 token を service/audit/log に渡さない。 |
| 安全な失敗 | Driver 失敗、想定外例外、body/content type 不正で stack trace、URI、password、token、原文、メール、絶対パスを response/audit/log に含めない。 |
| 監査 | 成功、absence、4xx、5xx の各イベントに必須許可フィールドのみがあり、件数・duration・request ID を照合できる。 |
| 既存互換 | `python3 -m unittest discover -v` と既存 Node test が通り、取込の冪等性、`RELATION.key`、既存 `trace()` を壊さない。 |

## 実装前に解消する未決事項

認証プロバイダ、token 検証担当 gateway、tenant/project 分離属性、監査 sink の耐久性方針、
プロダクションの rate limit 値は、リポジトリと Issue #9 に未登録である。本 Issue の API はこれらを
推測して独自実装せず、上記 Principal/audit interface を満たす接続先と運用決定を PM/運用担当から
受けてから外部公開する。決定までの実装範囲は、認証済み Principal を注入できる内部 adapter と
テストに限る。
