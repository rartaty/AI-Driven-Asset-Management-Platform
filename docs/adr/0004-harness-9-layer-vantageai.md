# ADR-0004: Claude Code ハーネス 9 層構成 (VantageAI 流儀準拠)

- **Status**: Accepted
- **Date**: 2026-05-02
- **Decided by**: ATSUYA TANAKA (プロジェクトオーナー兼ソロ開発者)
- **Related ADRs**: ADR-0001 (DB 選定 — 関連は薄いが一貫性のため)

---

## Context (背景)

Project Big Tester は Claude Code を主要な設計・実装支援ツールとして使用している。本PJ着手時 (2026-04-30) は `.claude/settings.json` の最低限のみで、CLAUDE.md / hooks / Skills / MCP / Memory 等が全て未整備または不十分だった。

別プロジェクト (VantageAI) で構築された **9層ハーネス設計** が完成度高く、本PJに転用可能と判定。流用の有無を判断する必要があった。

判断を保留すると:
- 毎セッションで同じ説明・確認を繰り返す
- 戦略コードに対する自動安全配線レビューが実装されない
- 過去の意思決定が memory に蓄積されず、毎セッションの起動コストが高い
- シークレット管理 / 不可逆操作 / REAL モード切替時の安全装置が存在しない

## Decision (決定)

**VantageAI 流儀の 9 層ハーネス設計を本PJに準拠採用**する。各層の構成は以下:

### 9 層の構成

| # | 層 | 本PJでの実装 |
| --- | --- | --- |
| 1 | settings.json | model=opus-4-7 / additionalDirectories=docs/ / defaultMode=default / 高リスク3ファイルを permissions.ask |
| 2 | CLAUDE.md & Rules | ハブ&スポーク (root/CLAUDE.md + backend/AGENTS.md + global ~/.claude/CLAUDE.md)・絶対禁止3条・@import 連鎖 |
| 3 | Skills | 3件 (`/strategy-template` opus / `/discord-notify` haiku / `/review` opus、全件 disable-model-invocation) |
| 4 | Subagents | 6件 (既存3改善 + 新規3: secrets-scanner / db-schema-reviewer / discord-notify-validator)。重要レビュー系3件に memory: project |
| 5 | Hooks | 計10件 Python統一 — プロジェクト6件 (PreToolUse/UserPromptSubmit/PostToolUse/SessionStart/Stop/SubagentStop) + グローバル4件 |
| 6 | MCP Servers | Phase A: filesystem-docs + sqlite-projectbig。Phase B/C (Context7 / GitHub / memory) は将来 |
| 7 | settings.local + .gitignore | settings.local.json (LOG_LEVEL=DEBUG) + .gitignore (85行) 整備。CLAUDE.local.md は taskboard.md Working Notes が代替 |
| 8 | Memory | 6ファイル (MEMORY.md index + user_role + project_current_focus + project_absolute_prohibitions + feedback_design_decisions + reference_docs) |
| 9 | シークレット管理 | 設計のみ確定 (本実装は Phase 4 統合)。ADR-0003 で SSM Standard 採用確定 |

### Why
- **完成度の高い参考設計が既に存在**: VantageAI で実証済み・本PJ要件と高い適合度
- **統合的安全装置**: Hooks / settings.json deny / subagent (secrets-scanner) で多重防御を構築
- **Memory による継続性**: セッションを跨いだ知識・判断の蓄積で説明コスト削減
- **絶対禁止3条の物理化**: REAL 切替・APIキー埋め込み・キルスイッチ解除を hooks レベルで監視

## Consequences (結果)

### ✅ Positive
- セッション開始時の説明オーバーヘッド大幅削減 (`MEMORY.md` 自動ロード + CLAUDE.md @import 連鎖)
- 戦略コード変更時に strategy-reviewer / db-schema-reviewer 等が自動チェック可能
- `.env` 編集 / 不可逆操作 / シークレットファイル編集を hook レベルで遮断
- Phase 4 着手時に必要な Subagent (secrets-scanner / discord-notify-validator) が既に配置済み
- VantageAI からの流用で実装時間を大幅短縮 (1日で 9 層完成)

### ⚠️ Negative / Trade-off
- **学習コスト**: ハーネス構造を理解するまでの初期投資
- **コンテキスト消費**: 全 .md 自動ロードでセッション初期コンテキストが膨らむ → 200行制限で緩和
- **Hook の保守**: Python スクリプト 10 件のメンテナンス負担 (将来追加でさらに増)
- **ベンダーロック (Claude Code)**: 別の AI コーディングツールへの移行時に手間

### 🔄 Required Follow-up
- ✅ 完了: 全 9 層実装 (2026-05-02)
- 📋 Phase 4: シークレット管理層 (Topic 9) の本実装 → ADR-0003 で詳細
- 📋 必要時: MCP Phase B (Context7) / Phase C (GitHub / memory) の追加 — MCP-1〜4
- 📋 git 化時: gitleaks pre-commit hook 配線 [SEC-7]
- 📋 運用安定後: ローテーション・監査ログ実装 [SEC-8, SEC-9]

## Alternatives Considered (検討した代替案)

### 案 X: 最小構成 (settings.json + CLAUDE.md のみ)
- **概要**: hook / Subagent / Skills / Memory 等を作らず、毎セッション説明
- **却下理由**: 戦略コード品質・安全配線・継続性の観点で大きく劣る・長期的に説明コストが膨大化

### 案 Y: 自前設計 (ゼロから組む)
- **概要**: VantageAI を参考にせず、本PJ独自に 9 層を再設計
- **却下理由**: 設計時間が膨大・実証済み流儀を捨てる合理性なし・「車輪の再発明」

### 案 Z: 別 AI ツール採用 (Cursor / Cline 等)
- **概要**: Claude Code から離れて別ツールへ
- **却下理由**: VantageAI の参考設計が Claude Code 前提・現時点で乗り換えメリット不明確

## Related (関連)

- ハーネスタスクボード: [.claude/harness-taskboard.md](../../.claude/harness-taskboard.md) (9層全て進捗管理)
- VantageAI 参考設計: [.claude/references/vantageai-harness-reference.md](../../.claude/references/vantageai-harness-reference.md)
- 関連 ADR: ADR-0003 (SSM 採用 — Topic 9 の決定)
- ルート: [CLAUDE.md](../../CLAUDE.md), [backend/AGENTS.md](../../backend/AGENTS.md), `~/.claude/CLAUDE.md`
- 関連 Memory: `~/.claude/projects/.../memory/feedback_design_decisions.md` #7

## Notes

### Hook 一覧 (Python統一・10件)
**プロジェクト** (`.claude/hooks/`):
- block-env-edit.py (PreToolUse、`.env*` ブロック)
- check-real-mode.py (UserPromptSubmit、REAL モード警告注入)
- lint-python.py (PostToolUse、ruff + mypy)
- session-start-summary.py (SessionStart、taskboard 表示)
- stop-activity-log-reminder.py (Stop、Activity Log 追記促し)
- save-agent-output.py (SubagentStop、agent-logs/ 保存)

**グローバル** (`~/.claude/hooks/`):
- global-session-info.py (SessionStart、日付/cwd/git branch 表示)
- global-block-destructive.py (PreToolUse Bash/PowerShell、不可逆操作確認注入)
- global-block-secrets.py (PreToolUse Edit/Write、~/.ssh/ 等への編集ブロック)
- global-stop-summary.py (Stop、不可逆操作実行時のサマリ報告促し)

### Subagent 一覧 (6件)
- strategy-reviewer (opus, memory: project)
- requirements-gap-detector (opus, memory: project) ← 本 ADR 作成のきっかけとなる R-2 監査を実行
- paper-trade-validator (sonnet)
- secrets-scanner (haiku)
- db-schema-reviewer (sonnet, memory: project)
- discord-notify-validator (haiku)
