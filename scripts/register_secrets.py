"""
対話式 SSM シークレット登録ヘルパー

参照仕様: docs/adr/0003-aws-ssm-standard-tier.md (SSM Standard tier 採用)
関連 Open Question: harness-taskboard.md SEC-4

使い方:
    python scripts/register_secrets.py                    # 対話モード (推奨・初回登録向け)
    python scripts/register_secrets.py --list             # 既存登録の一覧表示
    python scripts/register_secrets.py --bulk template    # サンプル雛形 (新規 PJ 用初期登録)

設計方針:
- パス命名規約: `/projectbig/{component}/{key-name}` ケバブ形式 (SEC-3 確定推奨案)
- シークレット入力は getpass で echo OFF (画面・履歴に残さない)
- 書き込み前に必ず確認プロンプト
- backend.src.core.aws_ssm の put_secret() を経由 (Tier='Standard' 強制パス)

⚠️ 平文でファイル保存・git コミットしないこと (CLAUDE.md 絶対禁止2)
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# backend/src を import パスに追加 (本スクリプトは scripts/ 配下から実行される想定)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "backend" / "src"))

from core.aws_ssm import describe_with_prefix, put_secret  # noqa: E402


# ===== 推奨命名規約 (SEC-3) =====
COMPONENT_KEY_TEMPLATE = {
    "kabucom": ["password", "api-password"],
    "opencanvas": ["client-id", "client-secret"],
    "discord": ["webhook-trading", "webhook-system", "webhook-alerts", "bot-token"],
    "anthropic": ["api-key"],
    "edinet": ["api-key"],
    "tdnet": ["api-key"],  # 将来 Yanoshin 等が要認証になった場合用
}


def _build_path(component: str, key: str) -> str:
    """`/projectbig/{component}/{key}` を構築 (両者を kebab-case 強制)."""
    norm = lambda s: s.strip().lower().replace("_", "-").replace(" ", "-")
    return f"/projectbig/{norm(component)}/{norm(key)}"


def cmd_list() -> int:
    """既存の `/projectbig/*` パラメータを一覧表示する (値は表示しない)."""
    try:
        params = describe_with_prefix("/projectbig/")
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1

    if not params:
        print("(no parameters registered under /projectbig/)")
        return 0

    print(f"\n=== Registered parameters under /projectbig/ ({len(params)} total) ===")
    print(f"{'Path':<60} {'LastModified':<22} {'Tier':<10}")
    print("-" * 92)
    for p in sorted(params, key=lambda x: x["Name"]):
        name = p["Name"]
        modified = p["LastModifiedDate"].strftime("%Y-%m-%d %H:%M:%S")
        tier = p.get("Tier", "Standard")
        print(f"{name:<60} {modified:<22} {tier:<10}")
    print()
    return 0


def cmd_register_one(component: str, key: str, value: str) -> int:
    """単一シークレットを SSM へ書き込む."""
    path = _build_path(component, key)
    try:
        put_secret(path, value, overwrite=True)
    except (ValueError, RuntimeError) as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    print(f"[OK] Registered: {path}")
    return 0


def cmd_interactive() -> int:
    """対話モード: コンポーネント選択 → キー選択 → 値入力 (echo OFF) → 確認 → 書き込み."""
    print("\n=== SSM Secret Registration (interactive) ===")
    print("Components:")
    components = list(COMPONENT_KEY_TEMPLATE.keys())
    for i, c in enumerate(components, 1):
        print(f"  {i}) {c}")
    print(f"  {len(components) + 1}) (custom — 自由入力)")

    raw = input("Select component number (or 'q' to quit): ").strip()
    if raw.lower() == "q":
        return 0
    if not raw.isdigit() or not (1 <= int(raw) <= len(components) + 1):
        print("[ERROR] Invalid selection.", file=sys.stderr)
        return 1

    idx = int(raw) - 1
    if idx == len(components):
        component = input("Custom component name (kebab-case): ").strip()
        key = input("Key name (kebab-case): ").strip()
    else:
        component = components[idx]
        keys = COMPONENT_KEY_TEMPLATE[component]
        print(f"\nKeys for '{component}':")
        for i, k in enumerate(keys, 1):
            print(f"  {i}) {k}")
        print(f"  {len(keys) + 1}) (custom)")
        raw_k = input("Select key number: ").strip()
        if not raw_k.isdigit() or not (1 <= int(raw_k) <= len(keys) + 1):
            print("[ERROR] Invalid selection.", file=sys.stderr)
            return 1
        kidx = int(raw_k) - 1
        key = input("Custom key name: ").strip() if kidx == len(keys) else keys[kidx]

    if not component or not key:
        print("[ERROR] Component and key are required.", file=sys.stderr)
        return 1

    path = _build_path(component, key)
    print(f"\nWill register to: {path}")

    # シークレット入力 (echo OFF)
    value = getpass.getpass("Enter secret value (will not be echoed): ")
    if not value:
        print("[ERROR] Empty value.", file=sys.stderr)
        return 1

    confirm = input(f"\nWrite to '{path}'? [y/N]: ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return 0

    return cmd_register_one(component, key, value)


def cmd_bulk_template() -> int:
    """初回セットアップ向け: 全コンポーネントの推奨キーを順次対話登録."""
    print("\n=== Bulk Template Registration ===")
    print("All keys defined in COMPONENT_KEY_TEMPLATE will be prompted in order.")
    print("Press Enter without value to skip individual keys.\n")

    for component, keys in COMPONENT_KEY_TEMPLATE.items():
        print(f"\n[Component: {component}]")
        for key in keys:
            path = _build_path(component, key)
            value = getpass.getpass(f"  {path} = ")
            if not value:
                print(f"  (skipped {path})")
                continue
            cmd_register_one(component, key, value)

    print("\n=== Bulk registration complete. Run with --list to verify. ===")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Interactive SSM secret registration helper for Project Big Tester"
    )
    parser.add_argument("--list", action="store_true", help="List existing /projectbig/* parameters")
    parser.add_argument("--bulk", choices=["template"], help="Bulk registration mode")
    args = parser.parse_args()

    if args.list:
        return cmd_list()
    if args.bulk == "template":
        return cmd_bulk_template()
    return cmd_interactive()


if __name__ == "__main__":
    sys.exit(main())
