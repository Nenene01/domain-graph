# Domain Graph

設計・開発工程で発生する業務、ドメイン、エンティティ、要件、機能、実装、課題、意思決定の関係と出所を、Neo4j のグラフとして構築・検索・検証するためのプロダクトです。

## 背景と目的

開発では、議事録、課題管理表、GitHub Issue、Pull Request、設計書、ソースコードなどに重要な情報が分散します。その結果、次のようなズレや漏れが発生します。

- 業務・ドメインの定義と実装の責務が一致しない
- エンティティや機能の出所、根拠、関連する意思決定を追跡できない
- 要件変更の影響範囲が分からない
- 設計と実装の不整合やレビュー漏れに気付けない

Domain Graph は、これらの情報をある程度構造化された入力として集約し、関係性と出所を回答できる状態にすることで、設計・開発構成の定義ズレや設計・実装ミスをできるだけ早く発見することを目指します。

## 想定する利用方法

入力情報をディレクトリ構成と命名規則に沿って保存し、フック、npm script、CLI、または AI エージェント／スキルで取り込みます。

```text
inputs/
  meeting-notes/       # 議事録、決定事項、未決事項
  issue-trackers/      # 課題管理表、スプレッドシートからのエクスポート
  github/              # Issue、Pull Request、レビュー記録
  requirements/        # 要件、業務ルール、ユースケース
  design/               # ドメイン設計、機能設計、API・データ設計
  source-metadata/      # ソースコードやテストとの対応情報
```

取り込んだ情報から、例えば次のようなノードと関係を構築します。

- ノード: 業務、ドメイン、エンティティ、要件、機能、設計要素、コード要素、Issue、PR、議事録、決定事項、担当者
- 関係: `DEFINED_BY`、`RELATES_TO`、`IMPLEMENTS`、`VERIFIES`、`BLOCKS`、`CHANGED_BY`、`DECIDED_IN`、`DERIVED_FROM`
- 属性: 出所、識別子、URL、リポジトリ、コミット、作成者、更新日時、信頼度、取り込み日時

利用者や AI エージェントは、例えば次の問いに答えを得られるようにします。

- この機能は、どの業務・要件・意思決定から発生したか
- このエンティティに関連する Issue、PR、設計、テストは何か
- 変更された要件の影響範囲はどこまでか
- 設計上定義されているが実装・テストされていないものは何か
- この回答の根拠となる原文や GitHub 上の情報はどこか

## 想定アーキテクチャ

```text
各種入力
  └─ 構造化・正規化・抽出
       └─ Neo4j グラフDB
            └─ 問い合わせ API / MCP Server
                 ├─ 設計者・開発者のローカル環境
                 ├─ Codex / Copilot / Claude Code のレビューエージェント
                 └─ GitHub Issue / PR の品質チェック
```

チーム運用では、クラウド上のコンテナまたは EC2 など、アクセス制御された閉じたインフラに Neo4j と API/MCP サーバーを配置します。ローカル PC や CI 上のエージェントからは、短期トークン、最小権限、監査ログを前提に接続できるようにします。

## 開発方針

1. すべての知識に出所（provenance）を持たせる
2. 自動抽出した事実と、人間が承認した定義を区別する
3. 回答には根拠となる入力、Issue、PR、コミットなどを必ず辿れるようにする
4. グラフの更新を冪等にし、同じ入力を再処理しても重複や破壊が起きないようにする
5. 秘密情報をグラフやログに取り込まず、接続認証情報は外部のシークレット管理に置く
6. まずは読み取りと可視化に集中し、コードや設計を自動変更する機能は明示的な承認を経て追加する

## 段階的なロードマップ

### Phase 1: 最小の知識グラフ

- Neo4j のローカル開発環境
- 議事録、要件、設計情報の Markdown/JSON 取り込み
- ノード・関係・出所の最小スキーマ
- Cypher による関係検索と回答用 API

### Phase 2: 開発ツール連携

- GitHub Issue / PR / commit の取り込み
- 課題管理表やスプレッドシートのインポート
- 入力検証、差分更新、重複排除
- MCP Server とローカル AI エージェント連携

### Phase 3: 品質検証とチーム運用

- PR 時の設計・実装整合性レビュー
- 未対応要件、孤立した設計、根拠のない実装の検出
- 認証・認可、監査ログ、テナント／プロジェクト分離
- クラウド上の閉じたインフラへのデプロイ

## 現時点で決めないこと

- 特定の AI ベンダーやエージェントへの固定
- 自動生成された関係を無条件に正しいとみなすこと
- 初期段階からの完全なコード理解や自動修正
- 機密情報を含むデータの無制限な外部 API 送信

この README はプロダクトのコンセプトと初期方針を記録するものです。実装仕様、グラフスキーマ、入力フォーマット、認証方式は今後の設計記録として追加します。

## MVP のローカル実行

`inputs/meeting-notes`、`inputs/requirements`、`inputs/design`、`inputs/source-metadata` に、`id`、`title`、`relations`、`provenance` を持つ JSON を配置します。`domain_graph.ingest.load_inputs()` が検証・正規化し、`Neo4jStore` は全処理を1トランザクションで実行します。ノードと関係は `MERGE`、`id` と関係キーはユニーク制約で冪等に取り込まれます。

```python
from domain_graph.ingest import load_inputs
from domain_graph.neo4j_store import Neo4jStore
from neo4j import GraphDatabase

driver = GraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "<secret-from-environment>"))
Neo4jStore(driver).ingest(load_inputs("inputs"))
```

サンプル入力では `REQ-ORDER-001` から `DEC-ORDER-001`、`MTG-2026-001`、`DSN-ORDER-001`、`CODE-ORDER-001` を provenance 付きで辿れます。問い合わせ対象が未登録の場合は `not_registered`、登録済みでも指定先への根拠がない場合は `evidence_insufficient` を返します。`python3 -m unittest discover -v` で主要な受け入れ条件を検証できます。

## Node.js 開発基盤と Neo4j ローカル環境

Node.js 22（`.nvmrc`）、TypeScript、Vitest、ESLint を使用します。Node 側の実装は
Neo4j 接続設定と疎通確認の境界を提供し、既存の Python MVP（`domain_graph/`）の
取り込み契約を置き換えません。

初回セットアップ:

```sh
cp .env.example .env
# .env の NEO4J_PASSWORD をローカル専用の値へ変更する
npm install
docker compose up -d --wait
npm run test:neo4j
npm test
npm run lint
npm run build
```

`NEO4J_URI`、`NEO4J_USERNAME`、`NEO4J_PASSWORD` は必須です。`.env` は Git の追跡対象外で、
パスワードはログやグラフ属性へ出力しません。Neo4j は Bolt `7687`、Browser `7474` を公開し、
`neo4j_data` と `neo4j_logs` の名前付き volume に保存します。停止する場合は次を実行します。

```sh
NEO4J_PASSWORD=<.env と同じ値> docker compose down
```

データも削除して初期化する場合のみ、同じく `NEO4J_PASSWORD=... docker compose down -v` を実行してください。
