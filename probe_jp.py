# -*- coding: utf-8 -*-
"""探測各候選日股來源在 GitHub Actions 雲端 IP 的可達性（HTTP 狀態）。一次性診斷用。"""
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

TESTS = {
    "kabutan_gainers":  "https://kabutan.jp/warning/?mode=2_1",
    "minkabu_up":       "https://minkabu.jp/financial_item_ranking/dprate_high",
    "minkabu_value":    "https://minkabu.jp/financial_item_ranking/turnover",
    "minkabu_rank_idx": "https://minkabu.jp/financial_item_ranking",
    "yahoojp_up":       "https://finance.yahoo.co.jp/stocks/ranking/up?market=all&term=daily",
    "yahoojp_value":    "https://finance.yahoo.co.jp/stocks/ranking/tradingValue?market=all&term=daily",
    "yahoojp_quote":    "https://finance.yahoo.co.jp/quote/7203.T",
    "yahoo_chart_api":  "https://query1.finance.yahoo.com/v8/finance/chart/7203.T?range=1mo&interval=1d",
    "stooq_csv":        "https://stooq.com/q/d/l/?s=7203.jp&i=d",
    "jpx_home":         "https://www.jpx.co.jp/",
    "nikkei_rank":      "https://www.nikkei.com/markets/ranking/",
    "traders":         "https://www.traders.co.jp/market_jp/ranking_detail/up_ratio",
}


def probe(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/json,*/*",
        "Accept-Language": "ja,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            body = r.read(4000)
            return f"OK {r.status}  bytes>={len(body)}  head={body[:90]!r}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code} {e.reason}"
    except Exception as e:
        return f"ERR {type(e).__name__}: {str(e)[:120]}"


if __name__ == "__main__":
    for name, url in TESTS.items():
        print(f"[{name}] {probe(url)}")
