import pytest
import sys
import os
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from api.edinet import EDINETAPIClient

def test_edinet_document_list_test_mode():
    client = EDINETAPIClient()
    # Ensure test mode is enabled
    client.test_mode = True
    
    docs = client.get_document_list(date(2023, 6, 20))
    assert len(docs) == 1
    assert docs[0]["secCode"] == "72030"
    assert docs[0]["filerName"] == "トヨタ自動車株式会社"

def test_edinet_download_and_parse_test_mode():
    client = EDINETAPIClient()
    client.test_mode = True
    
    # In test mode, S100TEST returns a dummy XBRL string
    xbrl_content = client.download_and_extract_xbrl("S100TEST")
    assert xbrl_content is not None
    assert "jppfs_cor:NetCashProvidedByUsedInOperatingActivities" in xbrl_content
    
    data = client.parse_financial_data(xbrl_content)
    
    # Expecting:
    # OpCF: 1500000000
    # InvCF: -500000000
    # Assets: 10000000000
    # EBITDA: OpIncome(1200000000) + Depreciation(300000000) = 1500000000
    
    assert data["operating_cf"] == 1500000000
    assert data["investing_cf"] == -500000000
    assert data["total_assets"] == 10000000000
    assert data["ebitda"] == 1500000000

def test_edinet_parse_empty():
    client = EDINETAPIClient()
    data = client.parse_financial_data("")
    assert data == {}

def test_edinet_parse_missing_tags():
    client = EDINETAPIClient()
    xbrl_content = """<?xml version="1.0" encoding="UTF-8"?>
    <xbrli:xbrl xmlns:jppfs_cor="http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2023-11-01/jppfs_cor" xmlns:xbrli="http://www.xbrl.org/2003/instance">
        <jppfs_cor:OperatingIncome contextRef="CurrentYearDuration">120</jppfs_cor:OperatingIncome>
    </xbrli:xbrl>
    """
    data = client.parse_financial_data(xbrl_content)
    # Only OperatingIncome is present (120), others are missing.
    assert data["operating_cf"] == 0
    assert data["investing_cf"] == 0
    assert data["total_assets"] == 0
    # ebitda = op_income (120) + depreciation (0) = 120
    assert data["ebitda"] == 120
