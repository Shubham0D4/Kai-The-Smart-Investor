"""
SEC XBRL company facts: annual financial statement numbers for Charlie and Gamma.

Fetches https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json and extracts the last
five fiscal years of a fixed set of line items, plus trailing-twelve-month (TTM) values built
from the latest 10-Q. Every value becomes an evidence record with the accession number of the
filing it came from. Network or parsing failures return success=False with an explicit error;
nothing is guessed.

Stock splits: a per-share value (EPS) or share count reported in a filing made BEFORE a split
is on the old share basis (seen live: NVDA's FY2022 EPS 3.85 from a filing before its 2024
10-for-1 split made a 4-year EPS growth look like 6% a year). When the split history is given,
such values are converted to today's share basis and the record says so.
"""

from datetime import date
from typing import Dict, List, Optional, Tuple

import httpx

SEC_HEADERS = {
    "User-Agent": "Kai-Economist contact@example.com",
    "Accept-Encoding": "gzip, deflate",
}
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn_nodash}/"

YEARS = 5

# metric -> (label, unit, [us-gaap concepts in priority order], kind)
# kind "duration" = income/cash-flow item over a fiscal year; "instant" = balance-sheet item at year end
METRICS: Dict[str, Tuple[str, str, List[str], str]] = {
    "revenue": ("Revenue", "USD", [
        "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"], "duration"),
    "gross_profit": ("Gross profit", "USD", ["GrossProfit"], "duration"),
    "operating_income": ("Operating income", "USD", ["OperatingIncomeLoss"], "duration"),
    "net_income": ("Net income", "USD", ["NetIncomeLoss"], "duration"),
    "eps_diluted": ("Diluted EPS", "USD/shares", ["EarningsPerShareDiluted"], "duration"),
    "operating_cash_flow": ("Operating cash flow", "USD", ["NetCashProvidedByUsedInOperatingActivities"], "duration"),
    "capex": ("Capital expenditure", "USD", ["PaymentsToAcquirePropertyPlantAndEquipment"], "duration"),
    "cash": ("Cash and cash equivalents", "USD", ["CashAndCashEquivalentsAtCarryingValue"], "instant"),
    "long_term_debt": ("Long-term debt (incl. current portion)", "USD", ["LongTermDebt"], "instant"),
    "commercial_paper": ("Commercial paper", "USD", ["CommercialPaper"], "instant"),
    "equity": ("Stockholders' equity", "USD", ["StockholdersEquity"], "instant"),
}


class SECDataError(Exception):
    pass


def _get_json(url: str) -> Dict:
    response = httpx.get(url, headers=SEC_HEADERS, timeout=20.0)
    response.raise_for_status()
    return response.json()


def lookup_cik(ticker: str) -> str:
    data = _get_json(TICKER_MAP_URL)
    t = ticker.upper()
    for entry in data.values():
        if entry.get("ticker") == t:
            return str(entry["cik_str"]).zfill(10)
    raise SECDataError(f"ticker {t} not found in SEC ticker map")


def _fiscal_label(end: str) -> str:
    """FY label from the period end date. 52/53-week years ending in the first days of
    January belong to the previous year."""
    d = date.fromisoformat(end)
    year = d.year - 1 if (d.month == 1 and d.day <= 7) else d.year
    return f"FY{year}"


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _annual_facts(concept: Dict, unit: str, kind: str, as_of: Optional[str] = None,
                  first_reported: bool = False) -> Dict[str, Dict]:
    """end date -> the most recently filed (or, with first_reported, the earliest filed) 10-K fact
    for that period. With as_of (ISO date), facts filed after it are ignored (no look-ahead)."""
    out: Dict[str, Dict] = {}
    for f in concept.get("units", {}).get(unit, []):
        if f.get("form") != "10-K":
            continue
        if as_of and f.get("filed", "") > as_of:
            continue
        if kind == "duration":
            if "start" not in f or not 340 <= _days(f["start"], f["end"]) <= 380:
                continue
        elif "start" in f:
            continue
        prev = out.get(f["end"])
        newer = f.get("filed", "") > (prev or {}).get("filed", "")
        if prev is None or (newer != first_reported and f.get("filed", "") != prev.get("filed", "")):
            out[f["end"]] = f
    return out


PER_SHARE = {"eps_diluted"}
TTM_METRICS = ("revenue", "net_income", "eps_diluted", "operating_cash_flow")


def split_factor(filed: Optional[str], splits: Optional[List[Dict]]) -> float:
    """Product of split ratios dated after the filing date (1.0 when none)."""
    factor = 1.0
    for sp in splits or []:
        if filed and sp["date"] > filed and sp.get("ratio"):
            factor *= float(sp["ratio"])
    return factor


def _adjust(rec: Dict, splits: Optional[List[Dict]], per_share: bool) -> Dict:
    """Put a per-share value or a share count on today's share basis."""
    factor = split_factor(rec.get("filed"), splits)
    if factor == 1.0:
        return rec
    rec = dict(rec)
    rec["value"] = rec["value"] / factor if per_share else rec["value"] * factor
    rec["label"] += f" (split-adjusted, {'/' if per_share else 'x'}{factor:g})"
    rec["source"] += f"; adjusted for stock splits after {rec.get('filed')} (x{factor:g})"
    return rec


def _quarterly_facts(concept: Dict, unit: str, as_of: Optional[str]) -> List[Dict]:
    return [f for f in concept.get("units", {}).get(unit, [])
            if f.get("form") == "10-Q" and "start" in f and (not as_of or f.get("filed", "") <= as_of)]


def _ttm(concept: Dict, unit: str, annual: Dict[str, Dict], as_of: Optional[str],
         splits: Optional[List[Dict]], per_share: bool) -> Optional[Dict]:
    """Trailing twelve months: the latest 10-K year if no 10-Q is newer; otherwise
    last fiscal year + year-to-date - the same year-to-date one year earlier."""
    if not annual:
        return None
    fy_end = max(annual)
    fy = annual[fy_end]
    ytd = [f for f in _quarterly_facts(concept, unit, as_of)
           if f["start"] > fy_end and f["end"] > fy_end and _days(fy_end, f["start"]) <= 7]
    if not ytd:
        return {"start": fy.get("start"), "end": fy_end, "parts": [fy], "how": "latest 10-K fiscal year",
                "value": float(fy["val"]) / (split_factor(fy.get("filed"), splits) if per_share else 1)}
    cur = max(ytd, key=lambda f: (f["end"], f.get("filed", "")))
    length = _days(cur["start"], cur["end"])
    prior = [f for f in concept.get("units", {}).get(unit, [])
             if "start" in f and (not as_of or f.get("filed", "") <= as_of)
             and abs(_days(f["start"], f["end"]) - length) <= 10
             and 350 <= _days(f["end"], cur["end"]) <= 380]
    if not prior:
        return None
    prev = max(prior, key=lambda f: f.get("filed", ""))
    adj = (lambda f: float(f["val"]) / split_factor(f.get("filed"), splits)) if per_share else (lambda f: float(f["val"]))
    value = adj(fy) + adj(cur) - adj(prev)
    start = (date.fromisoformat(cur["end"]).replace(year=date.fromisoformat(cur["end"]).year - 1)).isoformat()
    months = round(length / 30.4)
    return {"start": start, "end": cur["end"], "parts": [fy, cur, prev], "value": value,
            "how": f"FY to {fy_end} + {months}M to {cur['end']} - {months}M to {prev['end']}"}


def _record(metric: str, fact: Dict, cik: str, concept: str) -> Dict:
    label, unit, _, _ = METRICS[metric] if metric in METRICS else (metric, "USD", [], "instant")
    fy = _fiscal_label(fact["end"])
    accn = fact.get("accn", "")
    return {
        "id": f"sec:{metric}:{fy}",
        "label": f"{label} {fy}",
        "value": float(fact["val"]),
        "unit": unit,
        "period": f"{fact.get('start', '')}..{fact['end']}".lstrip("."),
        "source": f"SEC XBRL {concept}, {fact.get('form', '')} filed {fact.get('filed', '')} (accn {accn})",
        "url": ARCHIVE_URL.format(cik=int(cik), accn_nodash=accn.replace("-", "")) if accn else None,
        "filed": fact.get("filed"),
    }


def parse_company_facts(facts: Dict, cik: str, years: int = YEARS, as_of: Optional[str] = None,
                        splits: Optional[List[Dict]] = None) -> Dict:
    """Pure function: companyfacts JSON -> evidence records. Raises SECDataError if unusable.
    With as_of (ISO date) only facts filed on or before that date are used. With splits
    ([{date, ratio}]) per-share values and share counts are put on today's share basis."""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    if not gaap:
        raise SECDataError("company facts contain no us-gaap data")

    per_metric: Dict[str, Dict[str, Tuple[Dict, str]]] = {}
    for metric, (_, unit, concepts, kind) in METRICS.items():
        merged: Dict[str, Tuple[Dict, str]] = {}
        for concept in concepts:  # earlier concepts win; later ones fill gaps
            if concept in gaap:
                for end, f in _annual_facts(gaap[concept], unit, kind, as_of).items():
                    merged.setdefault(end, (f, concept))
        per_metric[metric] = merged

    # Fallback for long-term debt: noncurrent + current when the total is not tagged
    if "LongTermDebtNoncurrent" in gaap and "LongTermDebtCurrent" in gaap:
        nc = _annual_facts(gaap["LongTermDebtNoncurrent"], "USD", "instant", as_of)
        cur = _annual_facts(gaap["LongTermDebtCurrent"], "USD", "instant", as_of)
        for end in set(nc) & set(cur):
            if end not in per_metric["long_term_debt"]:
                f = dict(nc[end]); f["val"] = nc[end]["val"] + cur[end]["val"]
                per_metric["long_term_debt"][end] = (f, "LongTermDebtNoncurrent+LongTermDebtCurrent")

    # Fiscal year ends come from the income-statement anchors
    anchor_ends = set(per_metric["revenue"]) | set(per_metric["net_income"])
    if not anchor_ends:
        raise SECDataError("no annual (10-K) revenue or net income facts found")
    fy_ends = sorted(anchor_ends)[-years:]

    records: List[Dict] = []
    for metric, by_end in per_metric.items():
        for end in fy_ends:
            if end in by_end:
                f, concept = by_end[end]
                records.append(_adjust(_record(metric, f, cik, concept), splits, metric in PER_SHARE))

    # Trailing twelve months from the latest 10-Q (or the latest 10-K when it is newer)
    for metric in TTM_METRICS:
        _, unit, concepts, _ = METRICS[metric]
        concept = next((c for c in concepts if c in gaap), None)
        annual = {end: f for end, (f, c) in per_metric[metric].items() if c == concept}
        t = _ttm(gaap[concept], unit, annual, as_of, splits, metric in PER_SHARE) if concept else None
        if t is None:
            continue
        last = max(t["parts"], key=lambda f: f.get("filed", ""))
        label = METRICS[metric][0]
        records.append({
            "id": f"sec:{metric}:TTM",
            "label": f"{label}, trailing 12 months to {t['end']}",
            "value": round(t["value"], 6) if metric in PER_SHARE else t["value"],
            "unit": unit,
            "period": f"{t['start']}..{t['end']}",
            "source": f"SEC XBRL {concept}: {t['how']}"
                      + ("; split-adjusted" if metric in PER_SHARE and splits else ""),
            "url": ARCHIVE_URL.format(cik=int(cik), accn_nodash=last.get("accn", "").replace("-", ""))
                   if last.get("accn") else None,
            "filed": last.get("filed"),
        })

    # Current shares outstanding from the cover page (dei)
    dei = facts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {})
    share_facts = [f for f in dei.get("units", {}).get("shares", []) if not as_of or f.get("filed", "") <= as_of]
    if share_facts:
        latest = max(share_facts, key=lambda f: (f["end"], f.get("filed", "")))
        records.append(_adjust({
            "id": "sec:shares_outstanding:latest",
            "label": "Common shares outstanding",
            "value": float(latest["val"]),
            "unit": "shares",
            "period": latest["end"],
            "source": f"SEC XBRL dei:EntityCommonStockSharesOutstanding, {latest.get('form', '')} filed {latest.get('filed', '')}",
            "url": None,
            "filed": latest.get("filed"),
        }, splits, per_share=False))

    # Every annual diluted EPS as FIRST reported, with its filing date (for trailing PE at past
    # dates without look-ahead). Values are on the share basis of the filing date.
    eps_first = _annual_facts(gaap.get("EarningsPerShareDiluted", {}), "USD/shares", "duration",
                              as_of, first_reported=True)
    eps_history = [{"end": end, "filed": f.get("filed"), "value": float(f["val"])}
                   for end, f in sorted(eps_first.items())]

    return {
        "fiscal_years": [_fiscal_label(e) for e in fy_ends],
        "records": records,
        "eps_history": eps_history,
    }


def fetch_financial_statements(ticker: str, years: int = YEARS, splits: Optional[List[Dict]] = None) -> Dict:
    """
    MCP tool: last `years` fiscal years of financial statement numbers from SEC XBRL, plus TTM.
    splits ([{date, ratio}], e.g. from fetch_market_data) puts per-share values on today's basis.

    Returns {success, ticker, cik, company_name, fiscal_years, records[]} or
    {success: False, ticker, error}.
    """
    try:
        cik = lookup_cik(ticker)
        facts = _get_json(COMPANY_FACTS_URL.format(cik=cik))
        parsed = parse_company_facts(facts, cik, years, splits=splits)
        return {
            "success": True,
            "ticker": ticker.upper(),
            "cik": cik,
            "company_name": facts.get("entityName", ticker.upper()),
            **parsed,
        }
    except httpx.HTTPStatusError as e:
        return {"success": False, "ticker": ticker.upper(),
                "error": f"SEC request failed: HTTP {e.response.status_code} for {e.request.url}"}
    except (httpx.HTTPError, SECDataError, ValueError, KeyError) as e:
        return {"success": False, "ticker": ticker.upper(), "error": f"SEC financial data unavailable: {e}"}


def fcf_history(records: List[Dict]) -> List[Dict]:
    """Free cash flow per fiscal year = operating cash flow - capex (oldest first).
    [{fy, value, filed, ocf_id, capex_id}]"""
    ocf = {r["id"].rsplit(":", 1)[1]: r for r in series(records, "operating_cash_flow")}
    capex = {r["id"].rsplit(":", 1)[1]: r for r in series(records, "capex")}
    out = []
    for fy in sorted(set(ocf) & set(capex)):
        filed = max(x for x in (ocf[fy].get("filed"), capex[fy].get("filed"), "") if x is not None) or None
        out.append({"fy": fy, "value": ocf[fy]["value"] - capex[fy]["value"], "filed": filed,
                    "ocf_id": ocf[fy]["id"], "capex_id": capex[fy]["id"]})
    return out


def net_debt(records: List[Dict]) -> Dict:
    """Latest long-term debt + commercial paper - cash. Missing parts count as 0 and are listed.
    {value, filed, parts: [ids], missing: [metrics]}"""
    debt, cp, cash = latest(records, "long_term_debt"), latest(records, "commercial_paper"), latest(records, "cash")
    parts = [r for r in (debt, cp, cash) if r]
    value = sum(r["value"] for r in (debt, cp) if r) - (cash["value"] if cash else 0.0)
    filed = max((r.get("filed") or "" for r in parts), default="") or None
    return {"value": value, "filed": filed, "debt_ids": [r["id"] for r in (debt, cp) if r],
            "cash_id": cash["id"] if cash else None,
            "missing": [m for m, r in (("long_term_debt", debt), ("cash", cash)) if not r]}


def latest(records: List[Dict], metric: str) -> Optional[Dict]:
    """Most recent fiscal-year record for a metric (records are ordered oldest first; TTM and
    'latest' records are not fiscal years)."""
    found = [r for r in records if r["id"].startswith(f"sec:{metric}:")
             and not r["id"].endswith(":TTM")]
    return found[-1] if found else None


def series(records: List[Dict], metric: str) -> List[Dict]:
    return [r for r in records if r["id"].startswith(f"sec:{metric}:FY")]


if __name__ == "__main__":
    import json
    print(json.dumps(fetch_financial_statements("AAPL"), indent=2)[:3000])
