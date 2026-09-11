# Issue #7: 最小グラフスキーマと provenance モデル

## 決定

Phase 1 の正規形は、全ての知識を `:DomainNode`、全ての意味関係を
`:RELATION` として保存し、意味種別をそれぞれ `type` プロパティに置く。
これは既存 Python MVP の `DomainNode {id}`、`RELATION {key}`、および
`from|type|to` の関係キーを維持する互換優先の選択である。将来、関係種別ごとの
Neo4j relationship type を追加する場合も、少なくとも Phase 1 は `RELATION.type`
を正規の問い合わせ契約とする。

対象はスキーマ、制約、保存形、Cypher、テストであり、入力取込・API・GitHub の
ライブ連携は対象外である。`domain_graph/` の入力 JSON と Node.js の接続・疎通確認
の境界を変更しない。

## 根拠と既存契約

| 根拠 | 採用する制約 |
| --- | --- |
| `README.md` の Phase 1/MVP と開発方針 | 出所を辿れ、承認済み定義と自動抽出を区別し、再実行で重複させない。 |
| `PLAN.md` の共通受入条件 | AI の推論を事実扱いせず、`not_registered` と `evidence_insufficient` を明示する。 |
| `domain_graph/ingest.py` | ノード識別子はグローバルな `id`、関係識別子は `from|type|to`。既存の `IMPLEMENTS` は実装側から設計・要件側へ張る。 |
| `inputs/**/*.json` と `tests/test_ingest.py` | `REQ-ORDER-001` 等の ID、`DECIDED_IN`、`DERIVED_FROM`、`IMPLEMENTS`、provenance の `source_id`、`updated_by`、`retrieved_at` を維持する。 |
| `README.md` の Node.js 節 | Node 側は Neo4j 接続境界のみで、Python MVP の取込契約を置換しない。 |

Issue #1/#10 の完了内容として、既存 Python MVP の入力・追跡契約と Node/Neo4j
ローカル基盤を前提にする。外部 GitHub は参照しないため、本書の Issue 本文にない
追加要件は推測で補わない。

## ノード

全ノードに `:DomainNode` を付ける。ラベルを種類ごとに増やさず、`type` を列挙値に
するため、既存の一意制約と汎用クエリを保てる。

| `type` | 用途 | 例 |
| --- | --- | --- |
| `Business`, `Domain`, `Entity` | 業務、ドメイン、業務エンティティ | 注文業務、Order |
| `Requirement`, `Feature`, `Design` | 要件、機能、設計要素 | `REQ-ORDER-001`、`DSN-ORDER-001` |
| `Code`, `Test` | コード要素、検証コード | `CODE-ORDER-001` |
| `Issue`, `PullRequest`, `Commit` | 開発履歴（Phase 2 で使用） | GitHub Issue、PR、commit |
| `Meeting`, `Decision` | 原資料である議事録、意思決定 | `MTG-2026-001`、`DEC-ORDER-001` |
| `SourceDocument` | 上記に還元できない外部原資料の索引ノード | 仕様書、CSV export |

### 必須プロパティ

`id`, `type`, `title`, `approvalStatus`, `extractionMethod`, `confidence`, `sourceId`,
`sourceType`, `sourceLocator`, `retrievedAt`, `updatedBy`, `observedAt`, `createdAt`,
`updatedAt` を必須とする。`id` はプロジェクト内で不変・グローバル一意の文字列とし、
既存 ID を変更しない。`sourceLocator` はリポジトリ相対パス又は秘匿情報を含まない URL、
`sourceId` は原資料内の識別子、`observedAt` は原資料の更新・観測時刻、`retrievedAt` は
取得時刻、`createdAt`/`updatedAt` はグラフ記録時刻（ISO 8601 UTC）である。

`approvalStatus` は `approved` / `proposed` / `rejected` / `superseded` のいずれかとする。
既存 boolean `approved: true/false` はそれぞれ `approved` / `proposed` に写像する互換入力とし、
保存の正規値は `approvalStatus` とする。`extractionMethod` は `human_authored`、
`human_curated`、`deterministic_import`、`ai_inferred` のいずれか。AI 推論は必ず
`ai_inferred` かつ `approvalStatus=proposed` とし、人間承認されるまで定義・事実の根拠に
使わない。

`confidence` は 0.0〜1.0。人間が承認した定義は `1.0` を既定にしてよいが、原文の
真実性を保証する意味にはしない。`evidenceExcerpt` は根拠箇所の最小限の引用（秘匿情報・
個人情報を除去、または短い要約）であり、空文字は禁止する。原文全体、トークン、
メールアドレス等の個人情報は保存・ログ出力しない。長文は出所の安全な locator と
範囲（例: `sourceAnchor`）で参照する。

## provenance

provenance はノード・関係の両方に同じスカラー項目を複製して付与する。最低限は
`sourceId`, `sourceType`, `sourceLocator`, `sourceAnchor`, `sourceRevision`, `retrievedAt`,
`updatedBy`, `observedAt`, `extractionMethod`, `confidence`, `evidenceExcerpt` とする。
この情報は「関係が存在する根拠」であり、端点ノードの provenance を代用してはならない。

既存入力の `provenance.source_id`、`updated_by`、`retrieved_at` はそれぞれ camelCase の
正規項目へ変換する。ただし Python の正規化結果とテストが期待する snake_case の map は
入力・メモリ上の互換表現として残してよい。Neo4j のプロパティ値に map は保存できないため、
`SET n.provenance=$provenance` / `SET r.provenance=$provenance` を永続化の正規形にしては
ならない。Neo4j では上記をフラットなプロパティとして `SET` する（必要なら監査用の
`provenanceJson` 文字列を補助的に持つ）。この修正は既存 ID・関係キー契約を変更しない。

複数の独立した根拠を一つの関係に保持する必要が出た時点で、`Assertion` ノードを導入し
`(assertion)-[:ASSERTS {relationKey}]->(relation-evidence target)` に拡張する。Phase 1 で
根拠を配列プロパティや上書きで黙って失わないこと。現行の一関係一根拠というキー契約では、
競合する根拠は更新せず検出して `evidence_insufficient` として扱い、承認者の判断を待つ。

## 関係語彙と向き

すべて `(:DomainNode)-[:RELATION {type, key, ...provenance}]->(:DomainNode)` で保存する。
`key = fromId + '|' + type + '|' + toId` は不変であり、端点が存在する場合だけ `MERGE` する。

| `type` | 向き（左→右） | 意味 |
| --- | --- | --- |
| `DEFINED_BY` | 概念・業務・エンティティ → 定義・要件・設計 | 左の定義根拠が右に明記される。 |
| `RELATES_TO` | 原資料で主語として記された要素 → 相手要素 | 意味上は対称でも、重複防止のため原文の主語方向だけを一件保存する。 |
| `IMPLEMENTS` | Code/Design/Feature → Design/Requirement/Business | 左が右を実現する。既存 `CODE → DSN → REQ` を維持する。 |
| `VERIFIES` | Test/Review/検証結果 → Requirement/Feature/Design/Code | 左が右を検証する。成功結果・対象版・根拠を evidence に残す。 |
| `BLOCKS` | Issue/リスク/未決事項 → 妨げられる Requirement/Feature/Issue | 左が右の進行又は決定を阻害する。 |
| `CHANGED_BY` | 変更対象 → Issue/PullRequest/Commit/Decision | 左が右によって変更された。変更前後や版は provenance に残す。 |
| `DECIDED_IN` | Requirement/Design/Feature/Domain → Decision | 左についての決定が右でなされた。既存入力と同じ向き。 |
| `DERIVED_FROM` | Requirement/Decision/Design/抽出結果 → Meeting/SourceDocument/Requirement | 左が右の原資料又は上位根拠から導出された。既存入力と同じ向き。 |

語彙外の関係、未知の端点、曖昧な向きは取り込まず検証エラーとする。`RELATES_TO` の逆向きを
自動生成してはならない。探索時だけ両方向を許可してよいが、回答には保存された向きと根拠を
返す。

## 制約・冪等性・状態

```cypher
CREATE CONSTRAINT domain_node_id IF NOT EXISTS
FOR (n:DomainNode) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT domain_edge_key IF NOT EXISTS
FOR ()-[r:RELATION]-() REQUIRE r.key IS UNIQUE;

CREATE INDEX domain_node_type IF NOT EXISTS
FOR (n:DomainNode) ON (n.type);

CREATE INDEX relation_type IF NOT EXISTS
FOR ()-[r:RELATION]-() ON (r.type);
```

各実行は一トランザクション内で `MERGE (n:DomainNode {id:$id})` と
`MERGE (a)-[r:RELATION {key:$key}]->(b)` を行う。同一の ID/関係キーを再実行しても件数を
増やさない。`createdAt` は初回のみ、`updatedAt` は同一内容なら変更せず、内容差分がある時だけ
更新する。既存の人間承認済み値を `ai_inferred` の値で上書きしてはならない。差分競合は
監査可能なエラーにし、秘密値や根拠本文をログに出さない。

問い合わせは開始 ID が無ければ `not_registered`、開始は存在するが指定到達先又は承認済みの
根拠付き経路が無ければ `evidence_insufficient` を返す。後者は「関係が偽」と断定せず、
根拠不足・未承認・競合を理由として返す。AI 推論のみの経路は既定で根拠付き経路に数えない。

## サンプル Cypher

既存サンプルを正規化して保存する例（値はパラメータで渡す）。

```cypher
MERGE (req:DomainNode {id: 'REQ-ORDER-001'})
ON CREATE SET req.createdAt = datetime()
SET req.type = 'Requirement', req.title = '注文を登録できる',
    req.approvalStatus = 'approved', req.extractionMethod = 'human_curated',
    req.confidence = 1.0, req.sourceId = 'REQ-ORDER-001',
    req.sourceType = 'requirements', req.sourceLocator = 'inputs/requirements/REQ-ORDER-001.json',
    req.sourceAnchor = '$', req.retrievedAt = datetime('2026-01-15T00:00:00Z'),
    req.updatedBy = 'product-owner', req.observedAt = datetime('2026-01-15T00:00:00Z'),
    req.evidenceExcerpt = '注文を登録できる', req.updatedAt = datetime();

MATCH (req:DomainNode {id: 'REQ-ORDER-001'}),
      (decision:DomainNode {id: 'DEC-ORDER-001'})
MERGE (req)-[r:RELATION {key: 'REQ-ORDER-001|DECIDED_IN|DEC-ORDER-001'}]->(decision)
ON CREATE SET r.createdAt = datetime()
SET r.type = 'DECIDED_IN', r.approvalStatus = 'approved',
    r.extractionMethod = 'human_curated', r.confidence = 1.0,
    r.sourceId = 'REQ-ORDER-001', r.sourceType = 'requirements',
    r.sourceLocator = 'inputs/requirements/REQ-ORDER-001.json', r.sourceAnchor = '$.relations[0]',
    r.retrievedAt = datetime('2026-01-15T00:00:00Z'), r.updatedBy = 'product-owner',
    r.observedAt = datetime('2026-01-15T00:00:00Z'),
    r.evidenceExcerpt = 'REQ-ORDER-001 の決定事項参照', r.updatedAt = datetime();
```

承認済み・根拠付きの実装経路を返す例：

```cypher
MATCH p=(code:DomainNode {id:$codeId})-[:RELATION*1..6]->(req:DomainNode {id:$requirementId})
WHERE ALL(r IN relationships(p) WHERE r.type IN ['IMPLEMENTS','DERIVED_FROM','DECIDED_IN']
  AND r.approvalStatus = 'approved' AND r.evidenceExcerpt IS NOT NULL
  AND r.extractionMethod <> 'ai_inferred')
RETURN [n IN nodes(p) | {id:n.id, type:n.type, title:n.title}] AS nodes,
       [r IN relationships(p) | {key:r.key, type:r.type, sourceId:r.sourceId,
         sourceLocator:r.sourceLocator, evidenceExcerpt:r.evidenceExcerpt}] AS relations;
```

上の経路検索は関係の意味を無視した汎用探索である。プロダクト API では要求からコードを探す
逆向き探索を明示的に定義し、返却する関係の保存向きは反転しない。

## Luna 向け実装・テスト指示

1. 本書と `AGENTS.md` を読み、既存 Python 入力 ID、`ALLOWED_RELATIONS` の既存 5 種、
`RELATION.key`、Node の接続層を変更しない。8 語彙を使う検証拡張は、既存入力を通す後方互換を
保って追加する。
2. Neo4j 保存層だけで provenance map をフラットな Neo4j プロパティに変換する。入力形式と
`InMemoryGraph` が返す既存 provenance map を破壊しない。Node 側に取込機能を重複実装しない。
3. 保存時に `approvalStatus`、`extractionMethod`、`confidence`、`evidenceExcerpt` と時刻・
出所を必須検証する。旧 `approved` は明示的に正規化し、AI 推論を `approved` にしようとする
入力、範囲外 confidence、空の evidence、未知の関係を拒否する。
4. 制約は `IF NOT EXISTS` で作成し、ノード/関係を二回保存しても各キーが一件であることを
Neo4j integration test で確認する。接続不能時に秘密情報を出力しない。
5. テストは少なくとも、(a) 現在の `REQ→DEC/MTG←DSN←CODE` の追跡と provenance、(b) 全 8
関係の向き、(c) approved と ai_inferred/proposed の分離、(d) 二回取込の件数不変、(e) 未知端点の
ロールバック、(f) `not_registered` と `evidence_insufficient`、(g) evidence に秘密値・個人情報を
混入させない拒否、を観測する。
6. 実行報告には変更ファイル、`python3 -m unittest discover -v`、`npm test`、必要時の
`npm run test:neo4j` の結果、各受入条件、未解決の Assertion 拡張を記録する。仕様衝突又は
複数根拠の競合は独断で上書きせず PM に戻す。

## 受入観測

| 観測 | 合格条件 |
| --- | --- |
| 識別子・方向 | サンプル ID と `CODE → DSN → REQ`、`REQ → DEC/MTG` が変わらない。 |
| provenance | ノード・関係の各々から source ID、locator、取得時刻、抽出法、信頼度、根拠抜粋を取得できる。 |
| 事実の分離 | `ai_inferred/proposed` のみの経路は承認済みの根拠検索結果に含まれない。 |
| 冪等性 | 同一データ二回の保存後も node ID と relation key の件数が増えない。 |
| 不足状態 | 存在しない開始 ID は `not_registered`、承認根拠のない到達は `evidence_insufficient`。 |
| セキュリティ | テスト fixture、Cypher パラメータ、例外ログにパスワード、トークン、個人情報を置かない。 |
