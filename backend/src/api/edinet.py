"""
EDINET API クライアント
XBRLファイルをダウンロード・解析し、財務データ（CF、総資産、EBITDA）を抽出する。
"""

import os
import requests
import zipfile
import io
import logging
from bs4 import BeautifulSoup
from datetime import date
from typing import Optional, Dict, Any, List

from core.secrets import get_secret

logger = logging.getLogger(__name__)

class EDINETAPIClient:
    def __init__(self):
        self.base_url = "https://disclosure.edinet-fsa.go.jp/api/v2"
        # TRADE_MODE=PAPER のとき API はダミーデータを返却 (要件 §3 / Phase 5: TEST_MODE deprecate)
        self.test_mode = os.getenv("TRADE_MODE", "PAPER").upper() == "PAPER"
        try:
            self.api_key = get_secret("EDINET_API_KEY")
        except Exception as e:
            logger.error(f"[EDINET] Failed to get EDINET_API_KEY: {e}")
            self.api_key = ""

    def get_document_list(self, target_date: date) -> List[Dict[str, Any]]:
        """
        指定した日付の提出書類一覧を取得する
        """
        if self.test_mode:
            # テストモード時はダミーのドキュメントリストを返す（7203: トヨタ）
            return [
                {
                    "docID": "S100TEST",
                    "edinetCode": "E02144",
                    "secCode": "72030",
                    "filerName": "トヨタ自動車株式会社",
                    "docDescription": "有価証券報告書"
                }
            ]

        if not self.api_key:
            logger.warning("[EDINET] API key is missing. Cannot fetch document list.")
            return []

        url = f"{self.base_url}/documents.json"
        params = {
            "date": target_date.strftime("%Y-%m-%d"),
            "type": 2,
            "Subscription-Key": self.api_key
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
        except Exception as e:
            logger.error(f"[EDINET] Failed to fetch document list: {e}")
            return []

    def download_and_extract_xbrl(self, doc_id: str) -> Optional[str]:
        """
        指定したdocIDの書類(ZIP)をダウンロードし、XBRLファイルの内容を文字列で返す
        """
        if self.test_mode and doc_id == "S100TEST":
            # ダミーのXBRLデータを返す
            return """<?xml version="1.0" encoding="UTF-8"?>
            <xbrli:xbrl xmlns:jppfs_cor="http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2023-11-01/jppfs_cor" xmlns:xbrli="http://www.xbrl.org/2003/instance">
                <jppfs_cor:NetCashProvidedByUsedInOperatingActivities contextRef="CurrentYearDuration">1500000000</jppfs_cor:NetCashProvidedByUsedInOperatingActivities>
                <jppfs_cor:NetCashProvidedByUsedInInvestmentActivities contextRef="CurrentYearDuration">-500000000</jppfs_cor:NetCashProvidedByUsedInInvestmentActivities>
                <jppfs_cor:Assets contextRef="CurrentYearInstant">10000000000</jppfs_cor:Assets>
                <jppfs_cor:OperatingIncome contextRef="CurrentYearDuration">1200000000</jppfs_cor:OperatingIncome>
                <jppfs_cor:DepreciationAndAmortizationOpe contextRef="CurrentYearDuration">300000000</jppfs_cor:DepreciationAndAmortizationOpe>
            </xbrli:xbrl>
            """

        if not self.api_key:
            logger.warning("[EDINET] API key is missing. Cannot download document.")
            return None

        url = f"{self.base_url}/documents/{doc_id}"
        params = {
            "type": 1,
            "Subscription-Key": self.api_key
        }

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()

            # ZIPファイルをメモリ上で展開
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                # 拡張子が .xbrl のファイルを探す (PublicDoc配下)
                xbrl_filename = None
                for name in z.namelist():
                    if name.endswith(".xbrl") and "PublicDoc" in name:
                        xbrl_filename = name
                        break
                
                if xbrl_filename:
                    with z.open(xbrl_filename) as f:
                        return f.read().decode('utf-8')
                        
            logger.warning(f"[EDINET] No XBRL file found in ZIP for docID: {doc_id}")
            return None
        except Exception as e:
            logger.error(f"[EDINET] Failed to download or extract XBRL (docID: {doc_id}): {e}")
            return None

    def parse_financial_data(self, xbrl_content: str) -> Dict[str, Any]:
        """
        XBRLコンテンツから必要な財務項目を抽出する。
        - 営業CF (NetCashProvidedByUsedInOperatingActivities)
        - 投資CF (NetCashProvidedByUsedInInvestmentActivities)
        - 総資産 (Assets)
        - EBITDA = 営業利益(OperatingIncome) + 減価償却費(DepreciationAndAmortization)
        """
        if not xbrl_content:
            return {}

        soup = BeautifulSoup(xbrl_content, "xml")

        def get_value(tags: List[str]) -> float:
            # 複数のタグ名の候補から最初に一致したものを取得
            for tag in tags:
                element = soup.find(tag)
                # contextRef="CurrentYearDuration" or "CurrentYearInstant" を厳密に見るべきだが、
                # 簡易化のため最初にヒットしたものを返す（本番環境では文脈による絞り込みが必要）
                if element and element.text:
                    try:
                        return float(element.text)
                    except ValueError:
                        pass
            return 0.0

        # 日本基準 (jppfs) や IFRS (jpigp) のタグ候補
        op_cf = get_value(["jppfs_cor:NetCashProvidedByUsedInOperatingActivities", "jpigp_cor:CashFlowsFromUsedInOperatingActivities"])
        inv_cf = get_value(["jppfs_cor:NetCashProvidedByUsedInInvestmentActivities", "jpigp_cor:CashFlowsFromUsedInInvestingActivities"])
        assets = get_value(["jppfs_cor:Assets", "jpigp_cor:Assets"])
        
        operating_income = get_value(["jppfs_cor:OperatingIncome", "jpigp_cor:OperatingProfitLoss"])
        # 減価償却費（CF計算書上のものなどを参照）
        depreciation = get_value(["jppfs_cor:DepreciationAndAmortizationOpe", "jppfs_cor:Depreciation", "jpigp_cor:DepreciationAndAmortisationExpense"])

        ebitda = operating_income + depreciation

        return {
            "operating_cf": int(op_cf) if op_cf else 0,
            "investing_cf": int(inv_cf) if inv_cf else 0,
            "total_assets": int(assets) if assets else 0,
            "ebitda": int(ebitda) if ebitda else 0
        }
