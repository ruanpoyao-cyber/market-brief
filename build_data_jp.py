# -*- coding: utf-8 -*-
"""日股盤後資料產生器 → data_jp.json（與美股相同 schema，前端可切換）。
精簡檔數版：成交金額/漲幅/市值增加 各 10、跌幅/市值減少 各 5；不做產業樞紐分析。
資料來源：kabutan 排行頁選標的 + 逐檔個股頁取時價總額/業種/売買代金（候選池僅約 25 檔，降低限流）。
  - 漲幅 /warning/?mode=2_1   跌幅 /warning/?mode=2_2   成交額 /warning/trading_value_ranking
  - 個股頁 /stock/?code=XXXX（時價總額/業種/売買代金/OHLC）
  - 指數 日經225=0000、TOPIX=0010
新聞/分析：Gemini（GEMINI_API_KEY，選填）。kabutan 15 分延遲；盤後顯示前一交易日收盤。
被限流時保留前次 data_jp.json，不覆蓋、不崩潰。
"""
import os, json, time, re, datetime as dt, urllib.request, urllib.parse

RETAIN_DAYS = 60
GAIN_N, TURN_N, MUP_N = 20, 20, 20      # 上漲側各 20（原本檔數）
LOSE_N, MDN_N = 10, 10                  # 下跌側各 10
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
BASE = "https://kabutan.jp"
INDEXES = [("0000", "日經225", "N225"), ("0010", "TOPIX", "TOPIX")]
UAS = [
    UA,
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]


def _http(url, timeout=40, tries=5):
    last = None
    for i in range(tries):
        h = {"User-Agent": UAS[i % len(UAS)],
             "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
             "Accept-Language": "ja,en-US;q=0.8,en;q=0.6",
             "Referer": BASE + "/", "Connection": "close"}
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(min(2.5 * (i + 1), 18))
    raise last


def _f(x):
    if x is None: return None
    s = str(x).replace(",", "").replace("＋", "+").replace("－", "-").replace("%", "").strip()
    if s in ("", "-", "N/A", "--"): return None
    try: return float(s)
    except ValueError: return None


def _strip(html):
    s = re.sub(r"<[^>]+>", " ", html)
    s = s.replace("&nbsp;", " ")
    s = re.sub(r"&#?\w+;", " ", s)
    return s


def _oku_from_mcap(s):
    """'43兆8,548' / '23 兆 653' → 億円整數。"""
    s = re.sub(r"[,\s]", "", s)
    if "兆" in s:
        cho, rest = s.split("兆", 1)
        cho = re.sub(r"[^0-9]", "", cho) or "0"
        rest = re.sub(r"[^0-9]", "", rest) or "0"
        return int(cho) * 10000 + int(rest)
    n = re.sub(r"[^0-9]", "", s)
    return int(n) if n else None


# ---------- 排行頁（只取代號順序與漲跌）----------
def parse_ranking(html):
    m = re.search(r'<table class="stock_table[^"]*">(.*?)</table>', html, re.S)
    if not m:
        return []
    out = []
    for tr in re.findall(r"<tr>(.*?)</tr>", m.group(1), re.S):
        c = re.search(r'/stock/\?code=([0-9A-Z]{4})"', tr)
        nm = re.search(r'<th[^>]*class="tal"[^>]*>(.*?)</th>', tr, re.S)
        if not c or not nm:
            continue
        pct = re.search(r'class="w50">\s*<span[^>]*>\s*([+\-]?[0-9,\.]+)\s*</span>\s*%', tr)
        out.append({"code": c.group(1),
                    "name": re.sub(r"<[^>]+>", "", nm.group(1)).strip(),
                    "chg": _f(pct.group(1)) if pct else None})
    return out


def fetch_ranking(path, pages):
    rows, seen = [], set()
    for p in range(1, pages + 1):
        sep = "&" if "?" in path else "?"
        try:
            html = _http(BASE + path + sep + "page=" + str(p))
        except Exception:
            break
        got = parse_ranking(html)
        if not got:
            break
        for r in got:
            if r["code"] not in seen:
                seen.add(r["code"]); rows.append(r)
        time.sleep(0.3)
    return rows


def ranking_date(html):
    m = re.search(r"(\d{4})年(\d{2})月(\d{2})日\s*1[56]:00現在", html)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


# ---------- 個股頁（時價總額/業種/売買代金/價/漲跌）----------
def _short_en(s):
    """英語社名精簡：去尾 Holdings/Corporation/Co/Ltd/Inc/Group/K.K. 等法人字樣。"""
    s = (s or "").strip().strip(",.").strip()
    prev = None
    while prev != s and s:
        prev = s
        s = re.sub(r"[\s,]+(Holdings|Corporation|Incorporated|Company|Limited|Group|Corp|Inc|Co|Ltd|PLC|LLC|K\.?\s*K\.?|S\.A\.|N\.V\.)\.?$",
                   "", s, flags=re.I).strip()
    return re.sub(r"\s{2,}", " ", s).strip()


def fetch_stock(code):
    txt = _strip(_http(BASE + "/stock/?code=" + code))
    out = {"code": code}
    m = re.search(r"([0-9,]+\.?[0-9]*)円\s*前日比\s*([+\-][0-9,]+\.?[0-9]*)\s*([+\-][0-9.]+)\s*%", txt)
    if m:
        out["price"] = _f(m.group(1)); out["chg"] = _f(m.group(3))
    mc = re.search(r"時価総額\s*((?:[0-9,]+\s*兆\s*)?[0-9,]+)\s*億円", txt)
    out["mcap"] = _oku_from_mcap(mc.group(1)) if mc else None
    tv = re.search(r"売買代金\s*([0-9,]+)\s*百万円", txt)
    out["turnover"] = round(_f(tv.group(1)) / 100, 1) if tv else None
    sec = re.search(r"業種\s+(?!テーマ|単元|時価|単位)([^\s<0-9]{2,12})", txt)
    out["sector"] = sec.group(1).strip() if sec else "その他"
    en = re.search(r"英語社名\s+([A-Za-z0-9&'’.,\-/ ]{2,60}?)\s+(?:会社サイト|会社|概要|http|事業|代表)", txt)
    out["enname"] = _short_en(en.group(1)) if en else ""
    return out


def fetch_index(code, name, key):
    try:
        txt = _strip(_http(BASE + "/stock/?code=" + code))
    except Exception:
        return None
    m = re.search(r"([0-9,]+\.[0-9]+)\s*前日比\s*([+\-][0-9,]+\.?[0-9]*)\s*([+\-][0-9.]+)\s*%", txt)
    if not m:
        return None
    return {"key": key, "name": name, "value": _f(m.group(1)), "chg": _f(m.group(3))}


# ---------- 樞紐（依業種）----------
def pivot(lst):
    agg = {}
    for r in lst:
        a = agg.setdefault(r["sector"], {"sector": r["sector"], "count": 0, "sc": 0.0, "sm": 0.0, "st": 0.0})
        a["count"] += 1; a["sc"] += r["chg"]; a["sm"] += r["mcap_chg"]; a["st"] += r["turnover"]
    out = [{"sector": a["sector"], "count": a["count"], "avg_chg": round(a["sc"] / a["count"], 2),
            "mcap_chg": round(a["sm"], 1), "turnover": round(a["st"], 1)} for a in agg.values()]
    return sorted(out, key=lambda x: x["count"], reverse=True)


# ---------- AI 新聞/分析（Gemini，選填）----------
def ai_layer(movers, indices):
    api = os.environ.get("GEMINI_API_KEY")
    blank = {"ok": False, "news_summary": "（AI 未啟用：設定 GEMINI_API_KEY 後自動生成）", "news": [], "analysis": {}}
    if not api:
        return blank
    _seen = set(); movers = [m for m in movers if not (m["sym"] in _seen or _seen.add(m["sym"]))]
    lst = "\n".join(f"{r['sym']} {r['name']} {r['chg']:+.1f}%（{r['sector']}）" for r in movers)
    idx = "、".join(f"{i['name']} {i['chg']:+.2f}%" for i in indices)
    prompt = ("你是日股研究員。請用繁體中文，並搜尋中／日／英文新聞，完成：\n"
              "1) market_summary：約 130–170 字，聚焦『驅動昨日日股的事件與原因』——日銀政策／利率、日圓匯率、出口外需、重要財報、產業與政策、外資動向、關鍵個股催化等。**不要複述指數漲跌幅數字（使用者已從卡片看到），改說明背後成因、市場焦點與資金流向。**\n"
              "2) news：8 則影響今日重點日股標的的新聞，其中『中文來源最多 2 則』（如鉅亨網、經濟日報），其餘可為日文／英文／國際來源（日經、Bloomberg、Reuters、會社四季報）。每則含 title、source、url。"
              "**所有 title 一律輸出繁體中文；原文非中文必須翻譯，source 保留原始來源名稱，url 必須為真實可點擊連結。**\n"
              "3) analysis：對下列『每一檔』都要輸出（key=股票代號，繁體中文，1–2 句，精簡）。"
              "**以實際新聞與產業動態為主**：優先具體催化事件（財報、財測、訂單、併購、新產品、政策、匯率、供應鏈），查不到才用產業趨勢推測並標『推測／可能』。公司用簡稱。務必涵蓋每一檔。\n"
              f"指數：{idx}\n標的（代號 公司 漲跌% 業種）：\n{lst}\n"
              "只輸出 JSON：{\"market_summary\":\"\",\"news\":[],\"analysis\":{}}，不要其他文字。")
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "tools": [{"google_search": {}}],
               "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.5,
                                    "thinkingConfig": {"thinkingBudget": 0}}}
    base = "https://generativelanguage.googleapis.com/v1beta/models/"
    last_err = None
    for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
        url = base + model + ":generateContent?key=" + api
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                txt = json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"]
            j = json.loads(txt.strip().strip("`").lstrip("json"))
            if j.get("market_summary") or j.get("news"):
                return {"ok": True, "news_summary": j.get("market_summary", ""),
                        "news": j.get("news", []), "analysis": j.get("analysis", {})}
        except Exception as e:
            last_err = e
    return {**blank, "news_summary": f"（AI 暫時忙線：{last_err}）"}


def _dedup(rs):
    s, o = set(), []
    for r in rs:
        if r["code"] not in s:
            s.add(r["code"]); o.append(r)
    return o


def main():
    now_tpe = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)
    today = now_tpe.date().isoformat()

    try:
        g_html = _http(BASE + "/warning/?mode=2_1&page=1")
    except Exception as e:
        print(f"kabutan 無法存取（{e}）；保留前次 data_jp.json，本次不更新。")
        return
    session = ranking_date(g_html) or (now_tpe.date() - dt.timedelta(days=1)).isoformat()
    gain = _dedup(parse_ranking(g_html) + fetch_ranking("/warning/?mode=2_1", 2))  # 頁1+2，足 20 檔
    lose = fetch_ranking("/warning/?mode=2_2", 1)
    turn = fetch_ranking("/warning/trading_value_ranking", 1)
    print(f"排行抓取：漲{len(gain)} 跌{len(lose)} 成交額{len(turn)}（session={session}）")
    if len(gain) < 3 or len(turn) < 3:
        print("排行資料不足（疑似限流），保留前次 data_jp.json，本次不更新。")
        return

    # 候選池：漲幅20 ∪ 成交額25 ∪ 跌幅10（給市值增減一點空間），逐檔抓 ≈50 檔
    chg_rank = {r["code"]: r["chg"] for r in (gain + lose + turn) if r.get("chg") is not None}
    name_rank = {r["code"]: r["name"] for r in (gain + lose + turn)}
    pool, seen = [], set()
    for r in gain[:GAIN_N + 5] + turn[:25] + lose[:LOSE_N + 5]:   # 多抓幾檔緩衝（ETF無市值會被排除）
        if r["code"] not in seen:
            seen.add(r["code"]); pool.append(r["code"])

    stocks = {}
    for code in pool:
        try:
            s = fetch_stock(code)
        except Exception as e:
            print(f"  個股失敗 {code}: {e}"); continue
        chg = s.get("chg")
        if chg is None:
            chg = chg_rank.get(code)
        if s.get("price") is None or s.get("mcap") is None or chg is None:
            continue
        mcap = s["mcap"]
        disp_name = s.get("enname") or name_rank.get(code) or code   # 英文優先，無則日文
        stocks[code] = {"sym": code, "name": disp_name,
                        "price": round(s["price"], 2), "chg": round(chg, 2),
                        "mcap": round(mcap, 1), "mcap_chg": round(mcap * chg / 100.0, 1),
                        "turnover": s.get("turnover") or 0.0, "sector": s.get("sector") or "その他"}
        time.sleep(0.2)
    print(f"個股頁解析成功：{len(stocks)} 檔")
    if len(stocks) < 5:
        print("成功檔數過少（疑似限流），保留前次 data_jp.json，本次不更新。")
        return

    rows = list(stocks.values())
    gainers  = [stocks[r["code"]] for r in gain if r["code"] in stocks][:GAIN_N]
    losers   = [stocks[r["code"]] for r in lose if r["code"] in stocks][:LOSE_N]
    turnover = [stocks[r["code"]] for r in turn if r["code"] in stocks][:TURN_N]
    mcap_up  = sorted(rows, key=lambda r: r["mcap_chg"], reverse=True)[:MUP_N]
    mcap_dn  = sorted(rows, key=lambda r: r["mcap_chg"])[:MDN_N]

    idx_row = []
    for code, name, key in INDEXES:
        ix = fetch_index(code, name, key)
        if ix:
            idx_row.append(ix)
        time.sleep(0.2)
    print(f"指數：{[(i['name'], i['value'], i['chg']) for i in idx_row]}")

    ai = ai_layer(gainers + turnover + mcap_up + losers, idx_row)

    try:
        bundle = json.load(open("data_jp.json"))
    except Exception:
        bundle = {"symbols": {}, "indices_history": {}, "reports": {}, "dates": [], "streak3": {}, "axis": []}

    if not ai.get("ok"):
        _cand = ([today] if today in bundle.get("reports", {}) else [])
        _cand += sorted([x for x in bundle.get("reports", {}) if x != today], reverse=True)
        for _d in _cand:
            _prev = bundle["reports"][_d]
            if _prev.get("news"):
                if _d == today:
                    ai = {"ok": False, "news_summary": _prev.get("news_summary", ""),
                          "news": _prev.get("news", []), "analysis": _prev.get("analysis", {})}
                else:
                    ai = {"ok": False,
                          "news_summary": f"（AI 暫時忙線，沿用 {_d} 的新聞）　" + _prev.get("news_summary", ""),
                          "news": _prev.get("news", []), "analysis": {}}
                break
    for n in ai.get("news", []):
        if not str(n.get("url", "")).startswith("http"):
            n["url"] = ("https://news.google.com/search?q=" + urllib.parse.quote(n.get("title", "")) +
                        "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
    for r in gainers + mcap_up + turnover + losers + mcap_dn:
        r["analysis"] = ai["analysis"].get(r["sym"], "")

    dates_sorted = sorted(set(bundle.get("dates", []) + [today]), reverse=True)[:RETAIN_DAYS]
    bundle["reports"][today] = {
        "us_session": session, "market": "JP", "lite": False,   # 完整版：含樞紐與市值增減
        "indices": idx_row,
        "news_summary": ai["news_summary"], "news": ai["news"],
        "gainers": gainers, "mcap_up": mcap_up, "turnover": turnover,
        "losers": losers, "mcap_down": mcap_dn,
        "pivot_up": pivot(gainers), "pivot_up_mcap": pivot(mcap_up), "pivot_turnover": pivot(turnover),
        "pivot_down": pivot(losers), "pivot_down_mcap": pivot(mcap_dn)}
    bundle["dates"] = dates_sorted
    bundle["reports"] = {d: bundle["reports"][d] for d in dates_sorted if d in bundle["reports"]}
    bundle["streak3"] = {today: []}
    bundle["generated_at"] = now_tpe.strftime("%Y-%m-%d %H:%M") + " (台北)"
    bundle["color_convention"] = "INTL"
    bundle["market"] = "JP"
    bundle["lite"] = False
    json.dump(bundle, open("data_jp.json", "w"), ensure_ascii=False)
    print(f"完成 {today}：漲{len(gainers)} 市值增{len(mcap_up)} 成交{len(turnover)} "
          f"跌{len(losers)} 市值減{len(mcap_dn)} 指數{len(idx_row)} 新聞{len(ai['news'])}")


if __name__ == "__main__":
    main()
