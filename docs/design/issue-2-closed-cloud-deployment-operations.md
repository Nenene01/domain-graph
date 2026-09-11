# Issue #2: 閉じたクラウド環境へのデプロイと運用設計

## 結論・目的・根拠

GitHub Issue #2 の実際の題名は「閉じたクラウド環境へのデプロイと運用設計を行う」である。本書は、Domain Graph を閉じたクラウド環境で安全に提供・復旧・更新するための**実装指示**を定義する。外部に公開できるのは認証済み利用者向けの MCP/API 境界だけであり、Neo4j、取込 worker、バックアップ保管先、監査基盤、secret manager は private network 内に置く。クラウド事業者、アカウント、リージョン、CIDR、ネットワーク製品、IdP、KMS/HSM、CI 実行基盤、ログ・監査・バックアップの保管先と保持年数はリポジトリに未登録であり、本書は選定・値・接続先を推測しない。

| 根拠 | 本設計への反映 |
| --- | --- |
| `AGENTS.md` | provenance、冪等取込、秘密/PII 非保存、最小権限・短期 credential・監査、承認なしの自動変更禁止を運用にも適用する。 |
| `README.md` / `PLAN.md` Phase 4 | コンテナ又は EC2 相当の閉じた環境、短期 token、プロジェクト分離、バックアップ・監視・保持期間を整備対象とする。 |
| Issue #7 | `DomainNode.id` と `RELATION.key=from|type|to`、relation ごとの provenance、承認状態、既存8語彙と保存向きを移行・復旧でも不変とする。 |
| Issue #9 | `POST /v1/graph/traces` は read-only、固定・パラメータ化 Cypher、`not_registered` / `evidence_insufficient`、上限・timeout・安全なエラーを維持する。 |
| Issue #4 | MCP は3個の read-only tool の狭い公開境界、15分以下の token、scope/tenant/project、rate limit、監査を維持し、Neo4j を直接公開しない。 |
| Issue #6 | GitHub 同期は snapshot からの冪等取込であり、token/header/本文/メールを graph・監査・ログへ置かない。 |
| Issue #3 | CI/PR 検査は非破壊 report 作成に留め、GitHub への投稿・書込みは既定拒否、既存履歴の後勝ち更新を禁止する。 |
| 現行構成 | Node/TypeScript は `src/config.ts` / `src/neo4j.ts` の接続境界、Python は `domain_graph` の ingest/query/MCP/auth/audit、`compose.yaml` はローカル専用基盤である。 |

対象は production 相当のコード・IaC・運用手順・検証境界である。クラウドを実際に作成すること、データを移送すること、IdP を選ぶこと、外部公開すること、既存 API/MCP/取込契約を変えること、Neo4j Community の機能範囲を超える可用性保証を暗黙に実装することは対象外である。

## 論理構成と公開境界

```text
許可済み利用者 / CI の agent
  └─ TLS 終端・認証/認可・WAF/Rate limit の公開境界
       └─ MCP server (streamable HTTP) / GraphQueryApi
            └─ private application network
                 ├─ Python QueryService / McpToolService (read-only)
                 ├─ ingest / GitHub snapshot worker (非公開、承認済み job のみ)
                 └─ Neo4j Bolt (private data network、DB credential のみ)
                       └─ encrypted persistent data + backup/restore worker

監査 sink、secret manager、artifact/backup store、監視基盤
  └─ private endpoint 又は明示承認済みの閉域経路のみ
```

### 境界ごとの必須規則

| 境界 | 許可 | 拒否・禁止 |
| --- | --- | --- |
| 利用者 → MCP/API | TLS、短期 Bearer token を検証済み `Principal` に変換した read request。 | 平文通信、無認証、長期 token、任意 Cypher、Neo4j credential、ingest/write/admin API。 |
| MCP/API → Neo4j | private network からの固定 read query と、承認済み取込 job の固定 write query。別 DB principal を使う。 | Bolt/Browser のインターネット公開、agent が DB に直接接続、共有 DB 管理 credential の常用。 |
| CI → 実行環境 | 署名/承認済み artifact を deploy identity で昇格し、短期 credential で限定操作。 | 任意 branch の自動 production deploy、長期 cloud key、CI log への secret 出力。 |
| worker → 外部情報源 | allowlist 済み endpoint への GitHub snapshot 取得など、最小 scope・短期 credential。 | 任意 URL fetch、credential/本文/header の graph・ログ・監査保存。 |
| 運用者 → 管理面 | 個別の短期管理権限、監査、break-glass の承認・期限・事後レビュー。 | 共有 root/admin、恒久的な手作業 credential、監査なしの直接 DB 編集。 |

`compose.yaml` の `7474:7474` と `7687:7687` は README が説明するローカル開発用であり、production 用のポート公開設定として再利用してはならない。production は Neo4j Browser を無効又は private 管理経路に限定し、Bolt は application/maintenance identity のみを network policy と DB 権限の二重で許可する。MCP の stdio transport はローカル開発用途に限定し、クラウドでは TLS を終端でき認証・監査・制限を強制する transport のみを公開する。

## identity・credential・secret

### identity と最小権限

実装時は、人間又は workload ごとに識別可能な短期 identity を割り当てる。共有アカウント、static access key、`.env` を image・IaC state・CI variable・graph・監査イベントに保存してはならない。少なくとも次を分離する。

| identity | 最小権限 | credential の使途 |
| --- | --- | --- |
| gateway / MCP/API runtime | token 検証鍵の読取、必要時の audit 書込、QueryService 実行。DB read credential の取得のみ。 | 利用者 token の検証。token文字列は下流へ渡さない。 |
| ingest worker | 入力読取、GitHub snapshot 用の最小 read、Neo4j の ingest write credential、audit 書込。 | 承認済み manifest/job に限る取込。 |
| migration worker | 指定 version の schema migration と検証 query のみ。 | 通常 runtime と別 principal・期限付き job。 |
| backup/restore worker | DB backup/restore と暗号化済み保管先の限定 prefix のみ。 | backup catalog と復旧時だけ。 |
| deploy CI | artifact 読取、IaC plan/apply、対象環境への deploy。 | 保護 branch・承認済み release のみ。 |
| operator / break-glass | 承認済みの診断又は復旧に限定。 | 一回限り・短期限・監査必須。 |

Issue #4 の `domain-graph:read`、`domain-graph:read-proposed` を read 境界で維持する。`write-propose`、`write-apply`、`audit-read` を token が持っていても、初期の MCP tools は公開しない。既存の `Authenticator` が要求する issuer/audience/tenant/project policy は deployment configuration から供給するが、issuer、audience、claim 名、IdP、JWKS/introspection の方式は未決定であり、リポジトリ固定値や開発用 verifier を production に追加しない。

### secret manager とローテーション

- `NEO4J_PASSWORD`、page token HMAC key、GitHub credential、token-verification key/material、backup encryption key は external secret manager の versioned secret とし、runtime に必要な時だけ注入する。`src/config.ts` の環境変数契約はローカル互換のため保持し、production では secret manager の注入 adapter が同名の process 環境値又はファイル記述子へ安全に渡す。
- secret 値、Authorization header、cookie、JWT、DB URI の userinfo、private key、raw backup encryption key は IaC plan/state、container image、process list、exception、audit、debug dump、テスト fixture に含めない。マスク機構だけでなく、許可フィールド型を使って記録を防ぐ。
- rotation は新旧 version の重複有効期間、health/readiness、旧 version の失効、監査確認の順で行う。DB credential の rotation は read/runtime、ingest、migration/backup を一括更新せず、principal ごとに接続確認してから切替える。失敗・失効・secret manager 非到達は credential をキャッシュして延命せず、当該機能を `503 service_unavailable` として fail closed にする。
- rotation 周期、鍵の custody、緊急失効手順、secret manager の product/name/path は未決定事項である。

## 通信保護、認可、可用性の安全な失敗

TLS は利用者から gateway まで必須とし、TLS version/cipher、証明書発行・更新者、内部区間の mTLS 要否は承認済み platform standard を採用して設定として明記する。平文 HTTP、証明書検証無効化、自己署名の恒久利用、`--insecure` 相当の CI bypass は禁止する。private network 内でも network policy/security group 相当の default deny を使い、DNS・NTP・secret manager・IdP・監査・backup・許可済み情報源以外への egress は閉じる。

認証不能、期限切れ、issuer/audience/tenant/project/scope 不一致、監査を耐久的に受け渡せない状態、rate limit 超過、migration 中、readiness 未達、依存先不達は、情報を返さず書込みを開始せず fail closed とする。HTTP/MCP の安全な公開 code は既存契約に合わせ `unauthorized`、`forbidden`、`rate_limited`、`service_unavailable`、`query_timeout` 等だけとし、存在する別 tenant、host、内部経路、credential、stack trace を含めない。`not_registered` と `evidence_insufficient` は認可済みかつ ready な read request でだけ返す。

レート制限は gateway と `McpToolService.RateLimiter` の二層とし、前者は接続・本文サイズ・subject/tenant/client identity の濫用を DB 到達前に止め、後者は既存 tool 単位制限を維持する。具体的な秒間値、burst、WAF/CDN 製品は未登録であるため、load test と業務 SLO を根拠に構成値として決定するまで保守的な deny を採用し、超過 request は DB call 0 として `429` を返す。

## データ、migration、バックアップと復旧

### 不変性と migration

既存 `DomainNode`、`RELATION`、global `id`、`key=from|type|to`、8関係語彙、node/relation ごとの provenance、approval/extraction/confidence の意味を変更しない。migration はアプリ起動時に暗黙実行せず、versioned・review 済み・一回限りの migration job とする。各 migration は次を必須とする。

1. immutable release artifact 内の連番 ID、目的、前提 version、forward 操作、検証 query、rollback 手順を持つ。
2. apply 前に encrypted backup と restore drill 済みの基準点を確認し、migration plan/digest、operator/CI identity、開始・終了、結果を安全な audit に残す。
3. DDL/索引などの再実行可能操作は idempotent にし、data rewrite は旧レコードを上書き・削除しない。互換 read/write window、backfill、検証、旧経路の廃止を段階的に行う。
4. 失敗時は schema/data の状態を推測して再実行せず中断する。forward-only で安全に直せる場合は新 migration を追加し、戻す必要がある場合は backup restore 又は明示した compensation を使う。migration history を `git reset`、mutable tag、既存 migration ファイルの書換えで破壊しない。

tenant/project 属性を graph に永続化し shared DB 全 query の先頭から強制する方式、物理分離か論理分離か、Neo4j edition/cluster 方式は未決定である。これらが決まるまで、複数 tenant を同じ production DB に収容して外部公開してはならない。単一承認済み project の isolated deployment としてのみ稼働させる。

### backup、復旧、保持・削除

backup policy は、Neo4j data と復旧に必要な schema/migration version、application/IaC/image digest、設定の非秘密 digest を対応付ける。backup 本文に token、環境変数 dump、raw request/response、CI log を混在させない。保管先は runtime と異なる障害ドメイン、暗号化、最小権限、改ざん防止/削除保護、access audit を満たすものを選ぶが、具体的サービス・地域・RPO/RTO・頻度・retention は未決定である。

復旧 runbook は少なくとも (a) 障害を宣言し write/ingest/migration を停止、(b) 対象 backup の catalog/digest と migration version を照合、(c) 隔離環境で restore と integrity/provenance/constraint/代表 trace を検証、(d) 承認者が production 切替を承認、(e) gateway を readiness 済みの復旧系へ戻し、(f) audit・原因・失われる可能性がある時間帯を記録、の順とする。復旧中に古い backup を既存 DB へ上書きして履歴を消してはならない。元データと復旧先を保持し、切替は新しい instance/volume を作る方式を優先する。

保持・削除はデータ分類表と承認済み retention policy が必要であり、現時点では日数を推測しない。最低限、入力原本・正規化データ・graph assertion/provenance・GitHub snapshot manifest・audit・backup・deploy artifact を分類し、owner、法的/業務根拠、保持開始点、削除承認者、暗号消去/物理消去方式、backup からの失効時期を台帳化する。削除は承認済み job で対象 ID、根拠、影響関係、backup 上の残存、完了証跡を記録し、通常の ingest/migration/rollback が履歴を消す手段になってはならない。

## observability と runbook

ログは JSON の allowlist field を用い、request ID、opaque subject/token ID、tenant/project の許可済み ID、tool/route、outcome、HTTP status、件数、duration、release/config digest のみを原則とする。`audit.py` の interface を production sink へ差し替える際も、token、メール、本文、`evidenceExcerpt`、source locator の query/fragment、Cypher、Neo4j URI、absolute path、stack trace を出力しない。`MemoryAudit` と `NullAudit` は production sink に使わない。監査保存先、暗号化、閲覧者、tamper protection、保持年数は未決定であり、耐久キューへ enqueue 成功を確認できない場合は read response を返さない方針を既定とする。

health と readiness は分離する。liveness は process event loop が動作するだけでよく DB 接続を毎回要求しない。readiness は secret 注入済み、migration version 整合、Neo4j の認証済み read connectivity、監査の耐久的な書込/queue 受渡し、token verifier の必要依存、設定 schema/digest が満たされて初めて ready とする。readiness が false の instance は gateway から外し、ingest/migration/restore 中は意図的に false にする。health endpoint の path、認証要件、監視製品は未登録のため、public data を返さない private management endpoint として platform 設計で決める。

最低限の alert は、readiness 0、gateway 5xx/401/403/429 の異常増加、DB 接続/timeout、監査 enqueue/書込失敗、backup 失敗・期限超過、restore drill 未実施、migration 失敗、secret rotation 失敗/期限接近、certificate 期限、disk/volume 容量、rate-limit 拒否急増、CI deploy/rollback 失敗、依存脆弱性の未解消である。閾値・on-call・通知先は未決定なので IaC に仮の連絡先を埋め込まない。

| 事象 | 即時の安全な動作 | runbook の完了条件 |
| --- | --- | --- |
| Neo4j/監査/secret/IdP 不達 | readiness false、公開境界は 503、書込み job は開始しない。 | 原因解消後に最小権限接続・audit・代表 trace を検証してから再投入。 |
| token/tenant/scope 不正 | 401/403、DB call 0、token を記録しない。 | request ID と安全な監査だけで調査し、認可設定をレビュー。 |
| rate limit/DoS | 429、DB 到達前に遮断、容量を自動拡大しない。 | 正当な負荷か攻撃かを判定し、承認済み値のみ変更。 |
| migration 失敗 | gateway から外し、後続 migration を止める。 | backup/状態を検証し、forward fix 又は復旧計画を承認。 |
| backup/restore 異常 | 復旧可能と宣言しない。 | 隔離 restore と schema/provenance/代表 trace の一致を記録。 |
| credential 漏えい疑い | 該当 identity を失効・rotation、公開を必要なら閉鎖。 | 影響範囲、rotation、audit review、再発防止を承認。 |

## CI/CD、構成再現性、脆弱性管理、rollback

IaC は network、runtime、identity policy、secret reference（値ではない）、private endpoint、firewall/network policy、storage encryption、logging/audit、backup schedule、monitoring、deployment route を宣言的に管理する。state を使う方式では state の暗号化、最小閲覧者、lock、access audit、secret 値非格納を必須とする。環境差分は review 可能な非秘密 config file と secret reference に分離し、image digest、dependency lock (`package-lock.json`)、Python dependency lock を導入する場合の lock、migration version、IaC module/provider version、config digest を release manifest に固定する。最新 tag、手作業コンソール変更、mutable container tag、未固定 action/plugin は production の再現可能な根拠にしない。

CI は least-privilege の短期 federation を使い、pull request では lint/build/unit test と IaC validate/plan・secret scan・dependency/SBOM・image/IaC scan を実行する。production apply は保護 branch の immutable artifact/digest、review、環境承認、plan と apply 対象の一致を要求する。`npm ci`、`npm run lint`、`npm run build`、`npm test`、`python3 -m unittest discover -v`、可能な環境で `RUN_NEO4J_TESTS=true npm run test:neo4j` を既存の最低回帰に保つ。現行 CI は Node/Neo4j integration のみで Python test、secret scan、dependency scan、IaC check が未登録なので、追加は既存 workflow の契約を壊さない別 job/段階的必須化とする。

依存更新は lockfile を伴う PR、license/security advisory、テスト、SBOM、image scan、承認を経る。緊急脆弱性では影響 identity/network を先に閉じ、更新 artifact の provenance/digest を残す。自動更新 bot、許容 severity、SLA、脆弱性 DB/provider は未決定である。

deploy は immutable artifact の canary/段階的切替、readiness、代表の認可済み trace、監査 event、Neo4j read connectivity を満たしてから進める。rollback は直前の互換 artifact/config に traffic を戻す操作であり、DB history・backup・migration history・入力原本を削除又は巻戻ししない。backward-incompatible migration がある release は、互換 window と restore-tested rollback plan が承認されるまで deploy しない。

## Luna 向け実装範囲

### 実装してよいもの

| 区分 | Luna が追加する範囲 |
| --- | --- |
| コード | production config schema/validator、secret reference/injection interface、redacting logger、readiness/liveness adapter、graceful shutdown、audit durable-sink interface、migration runner interface。既存 `GraphQueryApi`、`McpServer`、`QueryService`、`Authenticator` の public request/response/tool 数を変えない。 |
| IaC | vendor-neutral module skeleton と variables/outputs、default-deny network、Neo4j 非公開、runtime identity 分離、secret reference、TLS termination、encrypted storage、backup/monitoring/audit hooks、environment-specific non-secret example。値未決定の変数は validation で apply を拒否する。 |
| runbook | deploy、rollback、secret rotation、backup、restore drill、migration、障害/credential incident、削除承認の手順と安全な証跡テンプレート。 |
| テスト | config の未設定/不正拒否、secret 非露出、readiness fail closed、rate-limit 前段拒否、scope/tenant 拒否、migration idempotence/中断、backup catalog validation、runbook command の dry-run/fixture 検証。 |
| 受入観測 | 下表の automation と、隔離環境での restore/deploy/rollback drill の記録形式。 |

### 実装してはならないもの

- 未決定のクラウド、リージョン、CIDR、DNS、IdP issuer/audience、KMS key、backup bucket、retention 日数、alert 宛先、rate-limit 値を仮値で production に固定すること。
- `.env`、secret、token、private key、DB password、実在 URL/tenant/project を repository、fixture、plan/state、ログ、graph に追加すること。
- Neo4j の public port、Browser、任意 Cypher、write MCP tool、token 発行 endpoint、外部 GitHub 投稿、shared DB tenant 分離を実装すること。
- 既存 graph record の破壊的 migration、`RELATION.key`/provenance/8語彙の意味変更、履歴を消す rollback、`compose.yaml` のローカル利用を production 設定で置換すること。

## テストと受入観測

| 観測 | 合格条件 |
| --- | --- |
| 公開境界 | 外部到達可能なのは TLS 終端後の MCP/API だけで、Neo4j Browser/Bolt、ingest、backup、audit、management endpoint は private network の許可 identity 以外から到達不能。 |
| 認可・credential | 期限切れ/不正 issuer-audience/scope/tenant-project の token は DB call 0 で 401/403。15分超 token、static key、secret manager 不達は fail closed。 |
| API/MCP 互換 | `POST /v1/graph/traces` と3 read-only tool、approved/proposed 分離、`not_registered` / `evidence_insufficient`、固定 query・既存上限が変わらない。 |
| ログ・監査 | 成功/absence/4xx/5xx/deploy/migration/backup/restore に相関可能な allowlist event があり、secret、PII、Cypher、本文、URI 詳細、stack trace が無い。監査 sink 故障時は成功応答しない。 |
| health | liveness と readiness を別に検査し、DB/監査/secret/migration 未整合で readiness false、gateway から除外される。 |
| backup/restore | catalog/digest/migration version を検証し、隔離 restore で constraint、node/relation count、provenance、`REQ-ORDER-001` からの代表 trace を確認できる。production 上書きはしない。 |
| migration/rollback | 同じ migration の再実行が安全、失敗で中断、forward-only history が残る。traffic rollback 後も graph/migration/backup 履歴は不変。 |
| CI/CD | immutable artifact と config/IaC digest で再現でき、PR の lint/build/test/scan と production の承認済み apply が分離される。 |
| 回帰 | `python3 -m unittest discover -v`、`npm test`、`npm run lint`、`npm run build`、可能なら `RUN_NEO4J_TESTS=true npm run test:neo4j` が通る。 |

## 実装前の決定待ち

以下は「未登録」であり、Luna は推測で補完せず PM/運用/セキュリティ承認を取得するまで production apply を停止する。

1. クラウド事業者、アカウント/組織、リージョン、可用性/災害復旧の障害ドメイン、container/VM/Neo4j edition・HA 方針。
2. network topology、CIDR、private endpoint/egress allowlist、DNS、TLS CA/certificate の管理者、公開入口の有無。
3. IdP、issuer/audience/claim mapping、token verifier/鍵 rotation・失効、tenant/project の物理・論理分離と migration 方針。
4. secret manager/KMS、key custody、rotation 周期、break-glass 承認者と監査保存先。
5. data classification、法令/契約上の保持・削除根拠、RPO/RTO、backup 頻度・保存先・保持年数、restore drill 周期。
6. SLO、rate-limit 値、監視/alert 製品、on-call/incident owner、脆弱性 severity/SLA、CI provider と production approval policy。

これらの決定は `docs/decisions/` 又は取り込み可能な `inputs/` 原本に、決定 ID、承認状態、source、取得/更新日時、適用環境、変更履歴を添えて記録する。決定が競合する場合は情報源・更新日時・承認状態を並べ、既存 #7 の provenance 原則に従って判断を保留する。
