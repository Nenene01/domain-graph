# Issue #4: MCP Server とエージェント向け認証の設計

## 結論、対象範囲、根拠

GitHub Issue #4 の実際の題名は「MCP Serverとエージェント向け認証を実装する」である。MCP は
インターネットへ Neo4j を直接公開せず、認証済みのエージェントから既存の根拠付き読み取り API を
呼ぶ**狭い公開境界**として実装する。初期公開は関係探索、出所確認、影響範囲確認だけの read-only
tools とし、書込み・任意 Cypher・トークン発行・GitHub 操作は公開しない。

| 根拠 | 設計への反映 |
| --- | --- |
| `AGENTS.md` | 最小権限・短期 token・監査、秘密/PII 非保存、AI 推論と人間承認の分離、自動変更の明示承認を必須にする。 |
| `README.md` / `PLAN.md` | MCP は Neo4j の後段に置き、根拠を辿る読み取りを優先する。テナント/プロジェクト分離は Phase 3/4 の要件である。 |
| Issue #7 | `DomainNode.id` と `RELATION.key=from|type|to`、8 関係語彙、relation ごとの provenance を正本とする。 |
| Issue #9 と `query_contract.py` / `query_service.py` | `POST /v1/graph/traces`、`domain-graph:read`、`domain-graph:read-proposed`、入力上限、固定 Cypher、safe error、`graph_trace` 監査を再利用する。 |
| Issue #11 の取込・`ingest.py` | approved/proposed と `ai_inferred` を混同せず、出所を持つ冪等な既存取込契約を変更しない。 |
| Issue #6 | GitHub snapshot は token/本文/メールを保存せず proposed で入る。MCP が既定で返さず、必要な場合だけ `read-proposed` で明示する。 |
| `src/config.ts` / `src/neo4j.ts` | Node.js は Neo4j 接続設定・疎通確認の基盤であり、Python 取込や query repository を Node に複製しない。 |

対象は、Python の query API を呼ぶ MCP transport/認可境界、scope と tenant/project 制約、監査、
レート制限、テスト、接続手順である。OIDC/JWT の発行者、鍵管理/KMS、IdP、配備方式、永続的な
テナント物理分割、書込み command の業務語彙は未登録であり、本 Issue で推測実装しない。

## 境界とデータフロー

```text
Codex / Copilot / Claude Code
  └─ MCP client (Bearer の短期 access token)
       └─ TLS + MCP server
            ├─ token 検証・scope/tenant/project 判定・rate limit・監査
            └─ GraphQueryApi / QueryService (Python)
                 └─ Neo4jQueryRepository (固定・パラメータ化 Cypher)
                      └─ Neo4j（非公開 private network）
```

- MCP server だけを API gateway/閉域 network に公開する。Neo4j Bolt/Browser と Python API の
  listen address は private network に限定し、agent token で Neo4j へ接続させない。
- MCP server は query API の唯一の agent-facing adapter とする。MCP tool が Neo4j driver、Cypher、
  ingest、GitHub credential provider に直接触れてはならない。
- 既存 `GraphQueryApi` の trusted `principal_provider` に、検証済み token claims から作る
  `Principal` を渡す。公開 HTTP API を併設する場合も同一の認可 service を通し、header を信用して
  Principal を生成してはならない。
- tenant/project filter は repository 境界で強制する。MCP 側だけの後段フィルタは、経路探索時に
  他 tenant の存在や件数を漏らすため禁止する。

## Principal、短期 token、scope

アクセストークンは署名検証可能な JWT 又は opaque token introspection のいずれかとするが、実装時に
一方式を選び、issuer/audience/鍵ローテーション/失効確認を文書化する。access token は 15 分以下、
clock skew は 60 秒以下とし、refresh token・client secret は MCP process、グラフ、監査、例外に
保存しない。長期 credential を必要とする agent は外部 secret manager/IdP から都度短期 token を
取得する。token の hash、JWT 全文、`Authorization` header、cookie、email、display name は監査
対象外である。

検証成功後に作る内部 principal は次だけを許可する。

```python
Principal(
  subject_id="opaque stable workload/user id",  # 128 文字以下、PII でない
  tenant_id="TENANT-ACME",                      # token claim と allowlist の一致を必須
  project_ids=frozenset({"PROJECT-ORDER"}),
  scopes=frozenset({"domain-graph:read"}),
  token_expires_at=utc_datetime,
  token_id="opaque non-secret id"               # 監査相関用。未発行なら null
)
```

現行 `query_contract.Principal` には `tenant_id` がないため、Luna はこの field を後方互換な
optional field として追加し、tenant 未対応 repository へ production Principal を渡すことを拒否する。
ローカル既存テストの `Principal` は `tenant_id=None` を許すが、MCP production 設定では fail closed
とする。`project_ids` は token claim からだけ採用し、MCP request parameter で増やせない。

| scope | 許可する tool / 振る舞い |
| --- | --- |
| `domain-graph:read` | approved の `graph_trace`、`graph_get_provenance`、`graph_impact`。 |
| `domain-graph:read-proposed` | `includeProposed=true` を明示した同じ read tool。結果に approval/extraction/confidence を必ず含める。 |
| `domain-graph:write-propose` | 将来の「変更提案作成」のみ。初期 MCP では token を受けても tool を公開しない。 |
| `domain-graph:write-apply` | 将来の承認済み適用のみ。初期 MCP では公開しない。 |
| `domain-graph:audit-read` | 将来の限定監査照会。初期 MCP では公開しない。 |

scope は文字列の部分一致・`*`・admin shortcut を許可しない。issuer/audience/exp/nbf/signature、
`sub`、tenant、少なくとも一 project、scope の型・allowlist をすべて検証し、一つでも不正なら
`unauthorized`、scope/tenant/project 不足なら存在有無を明かさず `forbidden` とする。

## tenant / project 分離

Issue #7 の node ID は現在グローバル一意で tenant 属性を持たない。このため初回の安全な実装は
**MCP deployment/database を tenant ごとに分離**し、token の `tenant_id` とサーバー設定の
`DOMAIN_GRAPH_TENANT_ID` が一致する場合だけ接続を許可する。プロジェクトは各 deployment の
allowlist と、将来追加する node/relation の必須 `tenantId` / `projectId` property の両方で制限する。

共有 DB を採用する判断があるまで、全 graph を横断する `Neo4jQueryRepository` を MCP から用いては
ならない。共有 DB を正式採用する場合の先行変更は、(1) node/edge の immutable `tenantId` と
`projectId` の provenance 付き取込、(2) tenant/project を全 `MATCH` と path 中の全 node/edge に
パラメータで必須化、(3) compound uniqueness/migration、(4) cross-tenant regression test、である。
これらがない状態では project claim は認可の根拠にせず、single-tenant deployment allowlist だけを
有効にする。

## MCP tool 契約

MCP protocol version/SDK/stdio・Streamable HTTP の正式選択は未登録である。ローカルでは stdio、
チーム環境では TLS 終端後の Streamable HTTP を候補とするが、同じ Authenticator と ToolService を
使う。stdio はローカル loopback 限定で、親 process が短期 token を安全に注入できる場合だけ許可し、
token を config file や tool argument に書かない。

初期 tool はすべて read-only であり、`additionalProperties: false` の JSON Schema、16 KiB 以下、
ID 128 字以下、depth 1..6、page size 1..100 を適用する。任意 Cypher、自由文検索、URL fetch、
raw provenance/graph dump は tool に存在しない。

| tool | 入力 | 下流 API / 出力 | 必須 scope |
| --- | --- | --- | --- |
| `domain_graph.trace` | #9 の `startId`, `targetId?`, `direction?`, `relationTypes?`, `maxDepth?`, `pageSize?`, `pageToken?`, `includeProposed?` | `QueryService.trace` の JSON をそのまま安全な MCP structured content として返す。保存向き、provenance、`not_registered`/`evidence_insufficient` を保持する。 | read。proposed 指定時は read-proposed も必要。 |
| `domain_graph.get_provenance` | `nodeId` 又は `relationKey` の排他的指定 | 新規の固定 repository query。単一の可視 node/relation の ID、状態、抽出法、confidence、allowlisted provenance を返す。本文・認証値は返さない。 | read。proposed 対象は read-proposed。 |
| `domain_graph.impact` | `startId`, `direction`（既定 both）、`relationTypes?`, `maxDepth?`, `pageSize?`, `pageToken?`, `includeProposed?` | `trace` と同じ固定探索。これは「到達可能な根拠付き候補」であり、変更影響の断定ではないことを `interpretation: "evidence-backed reachable elements"` で明示する。 | trace と同じ。 |

`get_provenance` の `relationKey` は自由に Cypher に連結せず、`from|type|to` と既存 ID/8語彙を
strict parse してから `$key` parameter として渡す。`trace` は既存の `parse_request` を唯一の
validator とし、tool adapter が別の緩い検証を実装しない。MCP error は JSON-RPC の一般失敗詳細を
露出せず、下表の public code と `requestId` だけを返す。

| 条件 | public code | 説明に含めないもの |
| --- | --- | --- |
| token 不在・期限切れ・署名不正 | `unauthorized` | issuer 内部理由、token 値、subject の存在 |
| scope/tenant/project 不足 | `forbidden` | node/relation の存在、許可 scope 一覧 |
| schema 不正 | `invalid_request` | 入力本文、parser/stack trace |
| 上限超過/timeout/rate limit | `query_limit_exceeded` / `query_timeout` / `rate_limited` | DB query、内部容量、Retry-After の内部値 |
| 未知 node・経路根拠なし | `not_registered` / `evidence_insufficient` | 非可視 tenant の存在 |
| 想定外 | `service_unavailable` | exception、host、path、driver detail |

## Cypher、入力、秘密情報の安全性

- `Neo4jQueryRepository` のように query text は定数にし、ID、relation type、tenant/project、limit
  をすべて driver parameter として渡す。label、relationship type、`ORDER BY`、property 名を client
  入力から組み立てない。任意 Cypher MCP tool、APOC 呼び出し、Neo4j Browser proxy を禁止する。
- repository は許可済みの fields だけを projection する。provenance は #9 の camelCase allowlist
  (`sourceId`, `sourceType`, `sourceLocator`, `sourceAnchor`, `sourceRevision`, `retrievedAt`, `updatedBy`,
  `observedAt`, `evidenceExcerpt`, `extractionMethod`, `confidence`) 以外を返さない。
- request/response/audit logger に body、title、evidence excerpt、source locator query/fragment、
  Authorization、token、cookie、Neo4j password、email、absolute path を渡さない。アプリ例外は safe code
  へ変換し、詳細は secret-safe な運用 telemetry にも原則保存しない。
- HTTP は TLS 必須、`Cache-Control: no-store`、request body size limit、JSON only、CORS allowlist
  （browser transport を選ぶ場合）、trusted proxy allowlist を適用する。MCP session ID は token ではなく
  ランダム値で、ログでは hash か request ID のみとする。

## レート制限と監査

認可成功後・repository 実行前に、`tenant_id + subject_id + tool` を key に token bucket を適用する。
初期既定値は subject 30 req/min・tenant 300 req/min、burst はそれぞれ 10/60、同一 trace は同時 2 件・
tenant 同時 20 件までとする。超過時は `rate_limited` を返し、query を開始しない。分散配備時は共有
rate-limit store が必要であり、未決定の間は単一 instance に制限して local memory limiter を使う。

監査は `mcp_tool_call` を一 call 一 event で、成功・認証失敗・認可拒否・validation/rate-limit/query
failure のすべてに書く。許可フィールドは次だけである。

```json
{
  "eventType": "mcp_tool_call",
  "occurredAt": "2026-09-12T00:00:00Z",
  "requestId": "opaque-request-id",
  "tool": "domain_graph.trace",
  "transport": "stdio|streamable_http",
  "tenantId": "TENANT-ACME",
  "projectCount": 1,
  "principalSubjectId": "opaque-subject-id",
  "tokenId": "opaque-token-id-or-null",
  "outcome": "success|error",
  "errorCode": null,
  "httpStatus": 200,
  "inputShape": {"hasTargetId": true, "maxDepth": 3, "pageSize": 25, "includeProposed": false},
  "returnedNodeCount": 4,
  "returnedRelationCount": 3,
  "durationMs": 12
}
```

監査 sink は既存 `AuditSink` を再利用し、#9 の `graph_trace` は変更しない。MCP event と下流
`graph_trace` は同じ `requestId` で相関可能にする。監査の永続先、暗号化、閲覧権限、保持/削除期間、
SIEM 連携は未登録であり、実装前に決定する。未決定の間も `MemoryAudit` を production に使わない。

## write 操作と明示承認フロー

初期 MCP server は `tools/list` に write tool を載せず、Neo4j credential も read-only DB user を使う。
将来 write を追加する場合も、agent の一 call で graph/入力/外部サービスを変更してはならない。

1. `domain_graph.propose_change` は `write-propose` scope を確認し、変更対象、差分、根拠 locator、
   影響見積り、request digest を immutable な**提案**として作る。自動抽出なら
   `ai_inferred/proposed`、approved data を上書きしない。
2. 人間承認者が別 UI/API で proposal ID、digest、tenant/project、根拠を確認し、短期 one-time
   approval token（5 分以下、proposal/digest/actor に束縛）を発行する。MCP が承認を自己申告する
   input は受理しない。
3. `domain_graph.apply_approved_change` は `write-apply` と承認 token の双方を検証し、再表示した
   digest が proposal と一致する時だけ一 transaction で適用する。期限切れ、再利用、競合、tenant 不一致
   は副作用ゼロで拒否し、承認/適用/拒否を監査する。

この workflow の proposal 保存モデル、承認者ロール、外部 GitHub 変更の可否、rollback/retention は
未登録であるため、本 Issue の実装では interface と deny-by-default のみを用意し、write endpoint は
実装しない。

## Luna 向け実装順序とファイル責務

1. `docs/design/issue-4-mcp-agent-auth.md`、`AGENTS.md`、Issue #6/#7/#9/#11 成果物、
   `domain_graph/api.py`、`query_contract.py`、`query_service.py`、`query_repository.py`、
   `src/config.ts`、`src/neo4j.ts` を読む。既存 Python ingest/query の public behavior と Node の
   接続基盤を変更しない。
2. 新規 `domain_graph/auth.py` に `TokenVerifier` protocol、strict claim validator、`AgentPrincipal`
   変換、scope allowlist、tenant deployment policy を置く。JWT library/IdP は adapter 注入に閉じ込め、
   secret 値を DTO/exception/logger に渡さない。
3. 新規 `domain_graph/mcp_contract.py` に tool JSON schema、public error mapping、relation-key parser、
   safe structured result を置く。`parse_request` を呼び、別の query validator を作らない。
4. 新規 `domain_graph/mcp_service.py` に認証→認可→tenant/project policy→rate limit→query service→
   audit の順を固定する ToolService を置く。`get_provenance` は固定・parameterized repository method
   だけを追加する。tenant-aware repository が未完成なら MCP startup を失敗させる。
5. 新規 `domain_graph/mcp_server.py` に MCP SDK transport adapter だけを置く。stdio/HTTP は同じ
   ToolService を使い、transport が DB driver を直接取得しない。SDK 選定後に lock file/dependency を
   追加し、Node.js で Python API を置換しない。
6. `query_contract.Principal` は optional `tenant_id`/`token_id` を追加する場合だけ最小変更する。
   `query_repository.py` は tenant/project を DB 側で制限できる実装に到達するまで shared DB を拒否する。
   `api.py`、`query_service.py`、GitHub import、`ingest.py` の既存 response/取込契約を変更しない。
7. 新規 `tests/test_mcp_auth.py`、`tests/test_mcp_service.py`、必要なら `tests/test_mcp_transport.py` を
   追加する。fixture は架空 ID と `example.invalid` だけを用い、JWT/token/password/email/本文を含めない。

接続手順は、(a) tenant 専用 private deployment を作る、(b) Neo4j read-only DB user と IdP audience を
server の外部 secret manager へ設定する、(c) agent に tenant/project/scope を絞った 15 分以下の token を
発行する、(d) MCP client が TLS endpoint 又は loopback stdio に token を process memory 経由で渡す、
(e) `tools/list` で read-only 3 tools のみを確認する、である。実際の env var 名、issuer URL、SDK、
secret manager は未登録のため README や committed config に仮値を置かない。

## 受入観測・テスト

| 観測 | 合格条件 |
| --- | --- |
| 公開境界 | MCP が read-only 3 tools だけを列挙し、Neo4j/Bolt、任意 Cypher、ingest/write tool を公開しない。 |
| 認証/認可 | 期限切れ、署名/issuer/audience 不正、scope 不足、tenant/project 不一致を拒否し、存在情報・token を返さない。 |
| token | 15 分超/clock skew 超の token を拒否し、token/header/secret が event、error、response、repr に無い。 |
| query 整合 | approved trace の node/relation/provenance/保存向き/status が #9 API と一致し、proposed は `read-proposed` + 明示指定時だけ返る。 |
| tenant | tenant A token で tenant B deployment/fixture を読む試行、project allowlist 外の試行とも `forbidden`。共有 DB を選ぶなら path 全体の DB filter を integration test で確認する。 |
| injection/validation | `startId`、relation key、page token、tool name に Cypher fragment、NUL、過大 JSON、未知 field を与えても固定 query 以外を実行せず safe error になる。 |
| rate/監査 | bucket 超過時に DB call 0、成功・失敗とも一 event、MCP と `graph_trace` の requestId 相関、許可外 field 非記録を確認する。 |
| write deny | `write-propose`/`write-apply` scope を持つ token でも初期 `tools/list`/call で書込みできず、Neo4j node/edge 数が不変。 |
| 回帰 | `python3 -m unittest discover -v`、`npm test`、`npm run lint`、`npm run build`、環境がある場合 `RUN_NEO4J_TESTS=true npm run test:neo4j` が通る。 |

## 未登録仕様・決定待ち

1. MCP SDK/protocol version と stdio/Streamable HTTP の正式採用、IdP/JWT 対 opaque token、issuer、
   audience、key rotation、失効、secret manager、TLS/mTLS を決定する必要がある。
2. tenant ID/project ID の正規識別子、shared DB への移行時期、既存 graph の migration、project 属性を
   どの入力/provenance から取得するかは未登録である。決定まで tenant 専用 deployment を維持する。
3. 監査の durable sink、保持期間、暗号化、閲覧者、rate limit の共有 store と運用値、availability/SLO
   は未登録である。
4. write proposal の保存先、承認者ロール、one-time approval token issuer、外部 GitHub 更新、rollback、
   Assertion/versioned assertion と複数根拠の扱いは未登録である。決定まで書込みを実装・公開しない。
