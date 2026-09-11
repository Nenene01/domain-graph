# Issue #8: 入力ディレクトリと Markdown/JSON 入力契約

## 決定と適用範囲

Phase 1 の入力は、情報源別の `inputs/<source>/` に置く一件一ファイルの JSON または
Markdown である。各ファイルは一つの Domain Graph レコード（1 ノードと、そのノードを
始点とする 0 件以上の関係）を表し、同一レコードを再処理しても既存の Python MVP の
ノード ID と関係キーを変えない。本書は Issue #7 の最小グラフスキーマを具体化する
入力契約であり、Issue #7 の ID、8 関係の語彙・向き、`from|type|to` の関係キー、
provenance の意味を変更しない。

対象はローカルの Markdown/JSON の検証・正規化契約である。GitHub API のライブ取得、
外部サービスへの送信、本格的な取込ジョブ、原文の編集は対象外とする。現行の
`domain_graph.ingest.load_inputs()` が読む JSON は後方互換の基準であり、Markdown と
拡張ディレクトリの対応は Luna が追加するまで仕様上の契約である。

## 根拠

| 根拠 | 本書への反映 |
| --- | --- |
| `README.md` の Phase 1 と MVP 実行節 | Markdown/JSON、情報源別配置、出所付きの冪等取込を定義する。 |
| `PLAN.md` の MVP/共通受入条件 | 根拠不足の明示、AI 推論との分離、再現可能な fixture と秘密情報非混入を要求する。 |
| `AGENTS.md` | 原文不変、取得日時・更新元・識別子の保持、推測による補完禁止を要求する。 |
| `docs/design/issue-7-minimum-graph-schema.md` | `DomainNode`/`RELATION`、必須 provenance、承認状態、8 関係と向きをそのまま採用する。 |
| `domain_graph/ingest.py` と `inputs/**/*.json` | 既存4ディレクトリ、snake_case の `provenance`、既存 ID、`approved` 互換入力を維持する。 |

## ディレクトリとファイル名

```text
inputs/
  meeting-notes/       # Meeting、Decision の原資料
  issue-trackers/      # Issue、課題表 export（将来のローカル fixture のみ）
  github/              # Issue/PR/review の保存済み export（ライブ連携なし）
  requirements/        # Requirement、Business、Domain、Decision
  design/              # Design、Feature、Entity
  source-metadata/     # Code、Test の対応メタデータ
```

`meeting-notes`、`requirements`、`design`、`source-metadata` は現行 Python MVP の必須
ディレクトリである。`issue-trackers` と `github` は README で予約された拡張用であり、
MVP ローダーが未対応の間はレコードを置かず、存在しても走査対象にしない。対応を追加する
時は明示的に `SOURCES` と既存テストを更新する。

ファイル名は `<record-id>.json` または `<record-id>.md` とし、`record-id` は本文の `id` と
完全一致させる。`id` は英大文字・数字・ハイフンからなる不変のプロジェクト内グローバル ID
（例: `REQ-ORDER-001`）とする。大小文字だけが異なるファイル、同じ ID の `.json` と `.md`、
同一ディレクトリ内の重複 ID はエラーである。補助ファイルは `_` で始め、入力走査から除外する。

原文を別名で正規化結果に置換しない。原資料はそのまま source directory に保存し、正規化済みの
中間成果物を将来保存する場合は `inputs/_normalized/` ではなく実行ごとのリポジトリ外作業領域に
置き、`source_locator` と原ファイルのハッシュで対応を記録する。原文を更新するのは情報源の
正規の更新者だけであり、取込処理は read-only である。

## 共通レコード契約

JSON は UTF-8、1 オブジェクトのみとする。Markdown は UTF-8 の YAML front matter をメタデータ
とし、本文を人間向け原文として保持する。両形式は次の正規レコードへ変換される。未記載の値を
AI やローダーが推測して補ってはならない（互換既定値は下表で明示したものだけ）。

| 項目 | 必須 | 規則 |
| --- | --- | --- |
| `schemaVersion` | 新規形式では必須 | 文字列 `1.0`。既存 JSON で欠落時のみ `1.0` として互換正規化し、警告ではなく互換記録を残す。未知の major version はエラー。 |
| `id`, `title`, `type` | 必須 | `id` はファイル名と一致。`title` は空白のみ不可。`type` は Issue #7 の node type 列挙値。既存 JSON の `type` 欠落だけはディレクトリ既定値を使う。 |
| `approvalStatus` | 必須 | `approved` / `proposed` / `rejected` / `superseded`。旧 `approved: true/false` は `approved` / `proposed` に変換する。両者が矛盾すればエラー。 |
| `extractionMethod` | 必須 | `human_authored` / `human_curated` / `deterministic_import` / `ai_inferred`。`ai_inferred` は必ず `approvalStatus: proposed`。 |
| `confidence` | 必須 | 数値 0.0〜1.0。人間承認済みの既定値は 1.0 にできるが、既存 JSON のみ互換既定を許す。 |
| `evidenceExcerpt` | 必須 | 根拠箇所の短い安全な引用又は要約。空文字、原文全文、秘密情報、メールアドレス等の個人情報を禁止する。 |
| `provenance` | 必須 | 下表の出所項目。既存 JSON は `source_id` / `updated_by` / `retrieved_at` を維持する。 |
| `relations` | 必須（空配列可） | このレコードを `from` とする関係の配列。各要素は `type` と `to` を必須とし、関係固有の `provenance` を任意で上書きできる。 |

`type` は `Business`、`Domain`、`Entity`、`Requirement`、`Feature`、`Design`、`Code`、`Test`、
`Issue`、`PullRequest`、`Commit`、`Meeting`、`Decision`、`SourceDocument` のいずれかとする。
関係の `approvalStatus` 等を省略した場合はノード値を継承するが、関係の provenance は
ノード provenance を根拠として暗黙流用してはならない。継承時は `source_anchor` を必ず関係を
記した箇所（JSON では `$.relations[n]`、Markdown では `#relations`）に正規化し、
`evidence_excerpt` は関係を支持する別の安全な抜粋を必須にする。

### provenance

provenance は入力では snake_case、Neo4j 保存時は Issue #7 の camelCase フラット属性へ変換する。

| 入力項目 | 必須 | 保存項目 | 意味 |
| --- | --- | --- | --- |
| `source_id` | 必須 | `sourceId` | 原資料内の識別子。ID と異なってもよい。 |
| `source_type` | 必須 | `sourceType` | 入力ディレクトリ又は原資料種別。 |
| `source_locator` | 必須 | `sourceLocator` | リポジトリ相対パス、又は秘匿情報を含まない URL。 |
| `source_anchor` | 必須 | `sourceAnchor` | JSONPath 又は Markdown 見出し・行範囲。 |
| `source_revision` | 任意 | `sourceRevision` | 原資料版、commit SHA 等（トークンを含めない）。 |
| `retrieved_at` | 必須 | `retrievedAt` | 取得日時（ISO 8601 UTC）。 |
| `updated_by` | 必須 | `updatedBy` | 更新元（人名・メールではなく役割又は安全な識別子）。 |
| `observed_at` | 必須 | `observedAt` | 原資料の更新又は観測日時（ISO 8601 UTC）。 |
| `extraction_method` | 必須 | `extractionMethod` | 抽出方法。上位項目と矛盾不可。 |
| `confidence` | 必須 | `confidence` | 信頼度。上位項目と矛盾不可。 |
| `evidence_excerpt` | 必須 | `evidenceExcerpt` | 安全に最小化した根拠。上位項目と矛盾不可。 |

既存 fixture の `source_id`、`updated_by`、`retrieved_at` はそのまま有効で、現在ローダーが
補う `source_locator`、`source_anchor`、`observed_at` 等は移行期間の互換既定とする。新規作成
では全項目を明示する。日時は UTC の `Z` を使い、パスは workspace 絶対パスにしない。

## 形式別の有効例

### JSON

```json
{
  "schemaVersion": "1.0",
  "id": "REQ-ORDER-002",
  "title": "注文を取消できる",
  "type": "Requirement",
  "approvalStatus": "approved",
  "extractionMethod": "human_curated",
  "confidence": 1.0,
  "evidenceExcerpt": "利用者は確定前の注文を取り消せる",
  "provenance": {
    "source_id": "REQ-ORDER-002",
    "source_type": "requirements",
    "source_locator": "inputs/requirements/REQ-ORDER-002.json",
    "source_anchor": "$.acceptanceCriteria[0]",
    "source_revision": "2026-09-12",
    "retrieved_at": "2026-09-12T00:00:00Z",
    "updated_by": "product-owner",
    "observed_at": "2026-09-12T00:00:00Z",
    "extraction_method": "human_curated",
    "confidence": 1.0,
    "evidence_excerpt": "利用者は確定前の注文を取り消せる"
  },
  "relations": [{
    "type": "DECIDED_IN",
    "to": "DEC-ORDER-002",
    "evidenceExcerpt": "取消可能期間は決定事項を参照する",
    "provenance": {
      "source_id": "REQ-ORDER-002",
      "source_type": "requirements",
      "source_locator": "inputs/requirements/REQ-ORDER-002.json",
      "source_anchor": "$.relations[0]",
      "retrieved_at": "2026-09-12T00:00:00Z",
      "updated_by": "product-owner",
      "observed_at": "2026-09-12T00:00:00Z",
      "extraction_method": "human_curated",
      "confidence": 1.0,
      "evidence_excerpt": "取消可能期間は決定事項を参照する"
    }
  }]
}
```

### Markdown

```markdown
---
schemaVersion: "1.0"
id: MTG-2026-002
title: 注文取消の決定事項
type: Meeting
approvalStatus: approved
extractionMethod: human_authored
confidence: 1.0
evidenceExcerpt: 取消可能期間を合意した
provenance:
  source_id: MTG-2026-002
  source_type: meeting-notes
  source_locator: inputs/meeting-notes/MTG-2026-002.md
  source_anchor: "#決定事項"
  retrieved_at: "2026-09-12T00:00:00Z"
  updated_by: product-owner
  observed_at: "2026-09-12T00:00:00Z"
  extraction_method: human_authored
  confidence: 1.0
  evidence_excerpt: 取消可能期間を合意した
relations: []
---

# 決定事項

取消可能期間を合意した。
```

Markdown 本文はグラフ属性に丸ごと保存しない。front matter の `evidenceExcerpt` と
`source_anchor` により、必要な原文箇所だけを安全に参照する。

## 無効例と統一エラー

| 無効入力 | 理由 | エラーコード |
| --- | --- | --- |
| `REQ-ORDER-002.md` 内の `id: REQ-ORDER-003` | ファイル名と ID が不一致 | `file_id_mismatch` |
| `schemaVersion: "2.0"` | 未対応 major version | `unsupported_schema_version` |
| `confidence: 1.2`、日時が UTC ISO 8601 でない | 型・範囲・日時が不正 | `invalid_field` |
| `ai_inferred` と `approvalStatus: approved` | 推論を人間承認済みとして扱う | `invalid_approval_state` |
| `relations: [{"type":"USES","to":"X"}]` 又は `to` が空 | 未定義関係・端点 | `invalid_relation` |
| `evidenceExcerpt: "token=..."` 又はメールアドレスを含む | 秘密情報・個人情報 | `sensitive_content` |
| 同一 ID の JSON と Markdown | どちらを正とするか不明 | `duplicate_record_id` |
| JSON 構文不正、front matter 不正 | パース不能 | `invalid_json` / `invalid_markdown_front_matter` |

検証エラーは機械可読な次の形式で返す。`message` とログには `evidenceExcerpt`、front matter
本文、トークン、メールアドレス、絶対パスを含めない。複数件は配列として収集し、取込は一件も
永続化しない。

```json
{
  "code": "invalid_relation",
  "path": "inputs/requirements/REQ-ORDER-002.json",
  "field": "relations[0].type",
  "message": "unsupported relation type",
  "recordId": "REQ-ORDER-002"
}
```

## 変換規則とグラフ対応

1. source directory、拡張子、ファイル名、UTF-8、schema version を検証する。
2. JSON はオブジェクトを、Markdown は front matter のオブジェクトを取り出す。本文から
   ノード、関係、根拠を推測抽出しない。
3. 旧 `approved`、既存 provenance の snake_case、明示済みの互換既定だけを正規化する。
   値が競合したらエラーとし、後勝ちで上書きしない。
4. ノードは `DomainNode {id}` に、関係は `RELATION {key: from|type|to}` に変換する。
   各々に独立した provenance をフラットな Neo4j property として付与する。
5. 同じ ID/関係キーを再実行しても `MERGE` する。内容が同一なら時刻を更新せず、承認済み
   人間定義を `ai_inferred` 又は未承認値で上書きしない。競合は `conflicting_record` として
   監査可能に失敗させる。

| 入力 | 正規化 | グラフ保存 | 注意 |
| --- | --- | --- | --- |
| レコードの `id/type/title` | node fields | `(:DomainNode {id})` の `type`, `title` | ID は不変・グローバル一意。 |
| レコードの承認・抽出・信頼度・根拠 | node assertion | node の `approvalStatus`, `extractionMethod`, `confidence`, `evidenceExcerpt` | AI 推論は approved 経路に含めない。 |
| レコードの provenance | node provenance | node の `sourceId`〜`observedAt` | map を Neo4j に保存しない。 |
| `relations[n].type/to` | edge `from=id`, `type`, `to` | `(:DomainNode)-[:RELATION {key}]->(:DomainNode)` | 向きと8語彙は Issue #7 の表に従う。 |
| 関係固有の assertion/provenance | edge assertion | relationship の同名フラット属性 | 端点ノードの根拠で代用しない。 |

関係の方向は、既存 `CODE-ORDER-001 → DSN-ORDER-001 → REQ-ORDER-001` の `IMPLEMENTS`、
`REQ-ORDER-001 → DEC-ORDER-001` の `DECIDED_IN`、`REQ/DEC → MTG` の `DERIVED_FROM` を
維持する。全8語彙の意味と方向は Issue #7 を唯一の正本とし、`RELATES_TO` の逆向きを自動で
増やさない。未知端点は検証後、同一トランザクション全体をロールバックする。

## セキュリティ、原文、冪等性

- パスワード、API key、access token、cookie、秘密鍵、メールアドレスその他の個人情報を、
  入力の graph-bound field、fixture、エラー、ログ、Neo4j 属性に置かない。検出時は内容を
  伏せた `sensitive_content` を返す。
- `source_locator` は安全な相対パス又は認証情報を含まない URL に限定し、URL query/fragment
  に秘密値がある場合は拒否する。原文本文・環境変数・認証設定を provenance に複写しない。
- 入力ファイルは取込で変更・整形・削除しない。取得日時は入力に記録された `retrieved_at` を
  正とし、ローダー実行時刻で元データを上書きしない。
- 冪等性の同一性は node `id` と relation `from|type|to`。同じ入力の再実行で件数は増えない。
  同一キーの内容差分と複数の独立根拠は黙って統合しない。Issue #7 の Assertion 拡張が未導入の
  間は、`evidence_insufficient` 又は `conflicting_record` として人間の判断を待つ。

## Luna 向け実装・テスト指示

1. 最初に本書、`AGENTS.md`、Issue #7、`domain_graph/ingest.py`、`tests/test_ingest.py` を読み、
   既存 IDs、`ALLOWED_RELATIONS`、`from|type|to`、snake_case provenance map と Python MVP の
   JSON 読込を破壊しない。Node.js 側には取込を重複実装しない。
2. JSON 契約の validator を schema version、ファイル名-ID、全 provenance、関係固有
   provenance、時刻、相対 locator、安全値まで拡張する。旧 fixture の欠落項目は明示的な
   `1.0` 互換正規化で通し、新規形式は厳格にする。
3. Markdown parser は YAML front matter のみをレコード化し、本文を推測解析しない。JSON と
   Markdown の同一 ID 重複、未対応ディレクトリ、未知 major version を明示エラーにする。
4. `InputValidationError` を上記の機械可読エラーに発展させる際も、既存呼出元が path を含む
   エラーを扱える後方互換を保ち、秘密値・本文を `str(error)` やログへ出さない。
5. テストには (a) 現在の4 JSON fixture が正常、(b) JSON/Markdown 各有効例、(c) ファイル名
   不一致・version・日時・関係・重複の無効例、(d) secret/PII のマスクされた失敗、(e) 関係ごとの
   provenance と全8方向、(f) 二回取込の件数不変、(g) 未知端点で全体ロールバック、(h) AI 推論が
   approved trace に入らない、を追加する。原文ファイルの内容・mtime を取込前後で比較する。
6. 実装後は `python3 -m unittest discover -v` を実行する。Neo4j 保存層を変更する場合だけ
   `npm test` と必要な Neo4j integration test も実行し、変更ファイル、受入観点、未解決の
   Assertion 拡張を報告する。

## 受入観測

| 観測 | 合格条件 |
| --- | --- |
| 配置・命名・版 | source directory と `<id>.(json|md)`、`schemaVersion: 1.0` が検証される。 |
| 形式 | JSON と Markdown の有効例、無効例、統一エラーが再利用可能な形である。 |
| 必須情報 | ID、承認状態、抽出方法、信頼度、根拠、source/provenance、更新元、取得日時を失わない。 |
| グラフ対応 | ノード/関係、8語彙の向き、関係キー、フラット provenance の対応表がある。 |
| 安全性 | 原文不変、推測補完なし、秘密情報・個人情報を保存・ログ出力しない。 |
| 互換・冪等性 | 既存 inputs/Python MVP と Issue #7 契約を維持し、再処理が重複を作らない。 |
