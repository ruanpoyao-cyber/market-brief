# -*- coding: utf-8 -*-
"""逆向 Yahoo 排行 rankingResult 結構 + minkabu 個股頁解析細節（Actions 雲端執行）。"""
import urllib.request, re, json

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "ignore")


def yahoo_results(slug):
    html = fetch(f"https://finance.yahoo.co.jp/stocks/ranking/{slug}?market=all&term=daily")
    m = re.search(r"__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", html, re.S)
    d = json.loads(m.group(1))
    return d.get("mainRankingList", {}).get("results", [])


def main():
    # 1) Yahoo 各排行 rankingResult 結構
    for slug in ("up", "down", "tradingValue", "marketCapitalHigh"):
        try:
            res = yahoo_results(slug)
            r0 = res[0] if res else {}
            print(f"[YAHOO {slug}] n={len(res)} item0={json.dumps(r0, ensure_ascii=False)[:400]}")
        except Exception as e:
            print(f"[YAHOO {slug}] ERR {e}")

    # 2) minkabu 個股頁解析
    try:
        H = fetch("https://minkabu.jp/stock/7203")
        T = re.sub(r"<[^>]+>", " ", H).replace("&nbsp;", " ")
        title = (re.search(r"<title>([^<]+)", H) or [None, ""])[1]
        en = re.search(r"\[([A-Za-z0-9&'’.\- ]{2,40})\]", title)
        mc = re.search(r"時価総額[^0-9]*([0-9,]+)\s*百万円", T)
        i = H.find("輸送用機器")
        print("[MINKABU] title=", title)
        print("[MINKABU] en_name=", en.group(1) if en else None)
        print("[MINKABU] mcap_oku=", round(int(mc.group(1).replace(',', '')) / 100, 1) if mc else None)
        print("[MINKABU] gyousyu_markup=", re.sub(r"\s+", " ", H[i - 140:i + 20]) if i >= 0 else "(none)")
        # 価格/前日比：找 (xxx) 形式或 円
        pm = re.search(r"([0-9,]+\.?[0-9]*)\s*円[^（(]{0,30}[（(]?\s*前日比\s*([+\-－][0-9,\.]+)[^%]{0,20}?([+\-－][0-9.]+)\s*[%％]", T)
        print("[MINKABU] price_block=", re.sub(r"\s+", " ", T[T.find('前日比') - 60:T.find('前日比') + 60]) if '前日比' in T else '(none)')
    except Exception as e:
        print("[MINKABU] ERR", e)


if __name__ == "__main__":
    main()
