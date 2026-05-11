# AWS Setup Guide — Project Big Tester

> 本PJ で AWS Systems Manager Parameter Store (SSM Standard tier) を月額 $0 で安全に運用するためのセットアップ手順。
> 関連 ADR: [ADR-0003](../adr/0003-aws-ssm-standard-tier.md) (SSM Standard tier 採用 / 5重防御)
> 関連 Open Question: harness-taskboard SEC-2 / SEC-3 / Phase 4 Step 3

---

## 0. 前提と全体像

### 前提
- AWS アカウント開設済 (SEC-0 完了)
- root ユーザーで AWS Console にログインできる
- Windows 11 + PowerShell 5.1 環境

### 9 セクション構成
| § | タスク | 所要 |
| --- | --- | --- |
| 1 | AWS CLI インストール + 初期確認 | 10 分 |
| 2 | アプリ専用 IAM ユーザー作成 (`projectbig-app`) | 10 分 |
| 3 | SSM 読取ポリシー作成 + アタッチ | 10 分 |
| 4 | SSM 書込ポリシー (Tier=Standard 強制) | 10 分 |
| 5 | KMS 復号権限の確認 | 5 分 |
| 6 | AWS Budgets 管理ポリシー (オプション) | 5 分 |
| 7 | AWS Budgets 設定 ($0.50 アラート / $1 自動 deny) | 15 分 |
| 8 | アクセスキー発行 + `aws configure` | 10 分 |
| 9 | 動作確認 + 初期 13 シークレット登録 | 15 分 |
| **合計** | | **約 90 分** |

### リージョン推奨
- **`ap-northeast-1` (東京)** を全セクションで使用 (本ドキュメント前提)

---

## §1. AWS CLI のインストール + 初期確認

### 1.1 ダウンロード + インストール (Windows)
1. ブラウザで https://awscli.amazonaws.com/AWSCLIV2.msi をダウンロード
2. MSI を実行 → 既定値でインストール
3. **新しい PowerShell** を開く (既存のは PATH 反映されない)

### 1.2 動作確認
```powershell
aws --version
```
**期待出力**:
```
aws-cli/2.x.x Python/3.x.x Windows/10 ...
```
**よくあるエラー**:
- `aws : 用語 'aws' は ...認識されません` → PATH 反映待ち。新しい PowerShell を開き直す or PC 再起動

---

## §2. アプリ専用 IAM ユーザー作成 (`projectbig-app`)

### 設計方針
- root ユーザーは日常使用しない (security best practice)
- アプリ・script 用に **権限を絞った IAM ユーザー** を作成
- プログラム的アクセスのみ (Console ログイン不要)

### 2.1 AWS Console から作成 (推奨)
1. AWS Console → **IAM** → 左メニュー **ユーザー** → **ユーザーの作成**
2. ユーザー名: `projectbig-app`
3. **AWS マネジメントコンソールへのアクセスを提供する** は **チェック外す** (プログラム的のみ)
4. 「次へ」→ アクセス許可は後で付与するので **「ポリシーを直接アタッチ」を選択しておく** (まだ何もアタッチしない)
5. 「次へ」→ 「ユーザーの作成」

**期待出力**: ユーザー一覧に `projectbig-app` が表示される。ARN は `arn:aws:iam::XXXXXXXXXXXX:user/projectbig-app` 形式。

---

## §3. SSM 読取ポリシー作成 + アタッチ

### 3.1 ポリシー JSON 作成
ローカルで以下を保存:

```powershell
@'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowSSMReadUnderProjectBigPrefix",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameter",
        "ssm:GetParameters",
        "ssm:GetParametersByPath",
        "ssm:DescribeParameters"
      ],
      "Resource": [
        "arn:aws:ssm:ap-northeast-1:*:parameter/projectbig/*"
      ]
    },
    {
      "Sid": "AllowDescribeAtAccountLevel",
      "Effect": "Allow",
      "Action": "ssm:DescribeParameters",
      "Resource": "*"
    }
  ]
}
'@ | Out-File -FilePath ".\projectbig-ssm-read-policy.json" -Encoding utf8
```

### 3.2 ポリシー作成 (CLI)
※ §8 (アクセスキー設定) より前なので、**Console** の方で作成する方が楽です。CLI の場合は管理者キーを別途用意。

**Console 手順**:
1. IAM → ポリシー → **ポリシーの作成**
2. **JSON タブ** に上記を貼り付け
3. 「次へ」→ ポリシー名: `projectbig-ssm-read`
4. 「ポリシーの作成」

### 3.3 ユーザーにアタッチ
1. IAM → ユーザー → `projectbig-app` → 許可 → **許可を追加** → **ポリシーを直接アタッチ**
2. `projectbig-ssm-read` を検索・選択 → 次へ → 許可を追加

**期待結果**: ユーザー詳細画面の「許可」タブに `projectbig-ssm-read` が表示される。

---

## §4. SSM 書込ポリシー (Tier=Standard 強制)

これが **5重防御 #3** の核 — Tier=Advanced を IAM レベルで拒否。

### 4.1 ポリシー JSON
```powershell
@'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowSSMWriteStandardOnlyUnderProjectBig",
      "Effect": "Allow",
      "Action": [
        "ssm:PutParameter",
        "ssm:DeleteParameter",
        "ssm:DeleteParameters",
        "ssm:AddTagsToResource"
      ],
      "Resource": [
        "arn:aws:ssm:ap-northeast-1:*:parameter/projectbig/*"
      ]
    },
    {
      "Sid": "DenySSMAdvancedTier",
      "Effect": "Deny",
      "Action": "ssm:PutParameter",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "ssm:Tier": "Advanced"
        }
      }
    },
    {
      "Sid": "DenySSMHigherThroughput",
      "Effect": "Deny",
      "Action": "ssm:UpdateServiceSetting",
      "Resource": "*"
    }
  ]
}
'@ | Out-File -FilePath ".\projectbig-ssm-write-policy.json" -Encoding utf8
```

### 4.2 作成 + アタッチ
- ポリシー名: `projectbig-ssm-write-standard-only`
- §3 と同じ手順で作成 + `projectbig-app` にアタッチ

### 4.3 動作確認 (§8 完了後に実行)
```powershell
# Standard tier (成功するはず)
aws ssm put-parameter --name "/projectbig/test/sample" --value "hello" --type SecureString --tier Standard --region ap-northeast-1

# Advanced tier (失敗するはず — DENY が効く)
aws ssm put-parameter --name "/projectbig/test/sample-adv" --value "hello" --type SecureString --tier Advanced --region ap-northeast-1
```
**期待**: 1つ目は成功、2つ目は `AccessDeniedException`。

---

## §5. KMS 復号権限の確認

### 5.1 結論: **追加設定は不要**

`SecureString` パラメータは AWS 管理 KMS キー `alias/aws/ssm` で暗号化される。このキーは **アカウント内の全 IAM プリンシパルが SSM サービス経由で自動的に使用可能**。

つまり:
- §3 で `ssm:GetParameter` を許可 = 暗号化値の復号も自動で可能
- 追加で `kms:Decrypt` を IAM ポリシーに書く必要なし

### 5.2 念のため確認 (§8 完了後)
```powershell
aws ssm get-parameter --name "/projectbig/test/sample" --with-decryption --region ap-northeast-1
```
**期待**: `Value` フィールドに `hello` が見える。`AccessDeniedException` が出る場合のみ §5 を再考 (カスタム CMK を使っている例外ケース等)。

---

## §6. AWS Budgets 管理ポリシー (オプション)

### 6.1 これは「Budgets を作る/更新する側」の権限
管理者作業 (§7) で必要。アプリ用 IAM ユーザー (`projectbig-app`) には付けない。**root ユーザーで作業するなら本セクションはスキップ可**。

### 6.2 別途管理者ユーザーを作る場合 (将来)
ユーザーが本セッション冒頭で示した snippet が該当:
```powershell
@'
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AllowBudgetsManagement",
            "Effect": "Allow",
            "Action": [
                "budgets:ViewBudget",
                "budgets:ModifyBudget",
                "ce:GetCostAndUsage",
                "ce:GetDimensionValues",
                "ce:GetTags"
            ],
            "Resource": "*"
        }
    ]
}
'@ | Out-File -FilePath ".\budgets-policy.json" -Encoding utf8

aws iam create-policy `
    --policy-name ManageAWSBudgetsPolicy `
    --policy-document file://budgets-policy.json `
    --description "Trading System: Manage AWS Budgets and read Cost Explorer data"
```

→ 個人運用フェーズでは root ユーザーが Budgets を管理すれば十分なので、このセクションは将来の拡張用ノート扱いに留める。

---

## §7. AWS Budgets 設定 ($0.50 アラート / $1 自動 deny)

これが **5重防御 #4** — 何らかの理由で課金トリガーが発火しても、AWS 側で強制停止する最後の砦。

### 7.1 Cost Explorer の有効化 (前提)
1. AWS Console → **AWS Cost Management** → **Cost Explorer** → **Cost Explorer を有効にする**
2. 24 時間ほど待つ (初回データ生成時間)

**注**: Cost Explorer 自体の課金は API 呼び出しに対してのみ ($0.01/リクエスト)。Console 上の操作は無料。

### 7.2 アラート Budget の作成 ($0.50)
1. **AWS Cost Management** → **Budgets** → **予算の作成**
2. テンプレート: **Customize (advanced)** → **Cost budget**
3. 予算名: `projectbig-monthly-cost`
4. 期間: **Monthly**, リセット日: 1日
5. 予算額: **$0.50** (USD)
6. しきい値:
   - 80% 実績達成 → email 通知 (= $0.40 で警告)
   - 100% 実績達成 → email 通知 (= $0.50 で警告)
7. メール通知先: 自分のメールアドレス
8. **予算アクション**: 後述 §7.3 で別途作成

### 7.3 自動 deny アクション ($1.00 で発火)
1. **Budgets** → 上記 `projectbig-monthly-cost` を選択 → **アクションを設定**
2. 通知タイプ: **実績**
3. しきい値: **$1.00** (絶対値)
4. アクションタイプ: **IAM ポリシーを適用する**
5. 適用先: ユーザー `projectbig-app` を選択
6. 適用するポリシー: 新規作成 (下記 JSON)

#### 緊急停止用ポリシー JSON
```powershell
@'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EmergencyDenyAllSSMWrite",
      "Effect": "Deny",
      "Action": "ssm:PutParameter",
      "Resource": "*"
    }
  ]
}
'@ | Out-File -FilePath ".\projectbig-emergency-deny.json" -Encoding utf8
```
- ポリシー名: `projectbig-emergency-deny-on-budget-breach`
- 説明: "Auto-attached when AWS Budget exceeds $1/month"
- このポリシーは Budget Action から **自動アタッチ** され、課金が下がっても **手動で外すまで残る** (= 「気づくまで止め続ける」設計)

7. 実行モード: **自動実行**
8. 「アクションの作成」

**期待結果**: Budgets 一覧に `projectbig-monthly-cost` が表示され、Action に Deny ポリシー登録済。

### 7.4 動作確認のテスト方法 (任意)
- 本番でテストするのはリスクなので、Budget しきい値を一時的に $0.001 にしてみる → アラートが届くことを確認 → $0.50 に戻す。

---

## §8. アクセスキー発行 + `aws configure`

### 8.1 アクセスキー発行
1. IAM → ユーザー → `projectbig-app` → **セキュリティ認証情報**
2. **アクセスキー** セクション → **アクセスキーの作成**
3. ユースケース: **コマンドラインインターフェイス (CLI)**
4. 確認 → **アクセスキーの作成**
5. **アクセスキー ID** + **シークレットアクセスキー** をメモ
   - ⚠️ シークレットキーは **この画面でしか表示されない**。閉じたら二度と見えない (再発行のみ)

### 8.2 `aws configure` 実行
```powershell
aws configure
```
プロンプトに従って入力:
```
AWS Access Key ID:     [先ほどコピーしたキー ID]
AWS Secret Access Key: [先ほどコピーしたシークレット]
Default region name:   ap-northeast-1
Default output format: json
```

これで `~/.aws/credentials` (Windows: `C:\Users\ATSUYA TANAKA\.aws\credentials`) に保存される。

### 8.3 動作確認
```powershell
aws sts get-caller-identity
```
**期待出力**:
```json
{
    "UserId": "AIDAXXXXXXXXXXXXXXXXX",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/projectbig-app"
}
```
**よくあるエラー**:
- `Unable to locate credentials` → `aws configure` 未実行 or タイポ
- `InvalidClientTokenId` → アクセスキーがコピペミス。再確認

---

## §9. 動作確認 + 初期 13 シークレット登録

### 9.1 SSM 接続確認
```powershell
aws ssm describe-parameters --parameter-filters "Key=Name,Option=BeginsWith,Values=/projectbig/" --region ap-northeast-1
```
**期待出力 (初回・空)**:
```json
{
    "Parameters": []
}
```

### 9.2 backend venv で boto3 インストール
```powershell
cd backend
.\venv\Scripts\Activate.ps1
pip install boto3 python-json-logger
```
**期待**: `Successfully installed boto3-X.X.X ...`

### 9.3 初期シークレット登録 (対話モード)
```powershell
cd ..
python scripts\register_secrets.py --bulk template
```
- 各キーごとにシークレット入力プロンプト (echo OFF・Tabキーで Skip)
- ユーザー報告通り、Discord webhook 4 件 + EDINET + Anthropic は既に登録済の場合は Skip でOK
- kabu STATION (申請中) は空のままで OK
- OpenCanvas (未着手) は空のままで OK

### 9.4 登録結果の確認
```powershell
python scripts\register_secrets.py --list
```
**期待出力 (例)**:
```
=== Registered parameters under /projectbig/ (6 total) ===
Path                                                         LastModified           Tier
--------------------------------------------------------------------------------------------
/projectbig/anthropic/api-key                                2026-05-03 18:42:11    Standard
/projectbig/discord/webhook-alerts                           2026-05-03 18:43:02    Standard
/projectbig/discord/webhook-system                           2026-05-03 18:43:08    Standard
/projectbig/discord/webhook-trading                          2026-05-03 18:43:15    Standard
/projectbig/edinet/api-key                                   2026-05-03 18:44:00    Standard
... (他のキー)
```

### 9.5 起動ラッパー確認
```powershell
.\scripts\load-secrets.ps1 -List           # マッピング確認 (値は取得しない)
.\scripts\load-secrets.ps1                 # 実際にロード
```
**期待出力**:
```
[OK]   ANTHROPIC_API_KEY              <- /projectbig/anthropic/api-key
[OK]   DISCORD_WEBHOOK_TRADING        <- /projectbig/discord/webhook-trading
[SKIP] KABUCOM_PASSWORD               (/projectbig/kabucom/password not found)
...
Loaded 6 secrets (4 skipped/missing)
```

### 9.6 apiKeyHelper 単体動作確認
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\get-anthropic-key.ps1
```
**期待**: Anthropic API キー文字列のみが標準出力される (改行・余分な装飾なし)。

---

## トラブルシューティング集

### `AccessDeniedException` (SSM 操作時)
- ポリシーが正しいリージョン (`ap-northeast-1`) を指定しているか
- `Resource` の `/projectbig/*` 配下を読もうとしているか (他のパスは権限なし)
- §4 の Tier=Advanced 拒否ポリシーが意図せず発火していないか

### `InvalidParameterException` (put-parameter 時)
- パラメータ名は `/projectbig/...` で始まっているか (`core/aws_ssm.py` のチェックに該当)
- 値が 4KB 以下か (Standard tier 制約)
- 値が空文字列でないか

### `ValidationException`
- Tier の値が `Standard` (大文字 S) になっているか
- `KeyId` が `alias/aws/ssm` (デフォルト) になっているか

### Budget アラートが届かない
- メール認証完了しているか (初回登録時のメール内リンク確認)
- Cost Explorer 有効化後 24 時間経過しているか

---

## 完了チェックリスト

- [ ] §1 AWS CLI 動作確認: `aws --version`
- [ ] §2 IAM ユーザー `projectbig-app` 作成
- [ ] §3 ポリシー `projectbig-ssm-read` 作成 + アタッチ
- [ ] §4 ポリシー `projectbig-ssm-write-standard-only` 作成 + アタッチ
- [ ] §4.3 Tier=Advanced put-parameter が deny される動作確認
- [ ] §5 SecureString 復号動作確認
- [ ] §7 Budget `projectbig-monthly-cost` 作成 + Action 設定
- [ ] §8 アクセスキー発行 + `aws configure`
- [ ] §8.3 `aws sts get-caller-identity` 成功
- [ ] §9.2 `pip install boto3 python-json-logger` 成功
- [ ] §9.3 初期シークレット登録完了 (kabu/OpenCanvas は申請完了後)
- [ ] §9.5 `load-secrets.ps1` 動作確認
- [ ] §9.6 `get-anthropic-key.ps1` 動作確認

完了後、Phase 4 Step 5 (`core/discord.py` 本実装) へ進む。
