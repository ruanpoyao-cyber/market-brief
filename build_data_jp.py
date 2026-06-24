# -*- coding: utf-8 -*-
"""日股盤後資料產生器（Yahoo Finance Japan 核心版）→ data_jp.json。
kabutan/minkabu 已封鎖 GitHub Actions 雲端 IP；改用 Yahoo（排行頁內嵌 __PRELOADED_STATE__ JSON、不擋雲端）。
功能：漲幅前20 / 跌幅前10 / 成交額前20、日經225+TOPIX K線、Gemini 繁中新聞。
族群＝市場別（主板/標準/成長）；本版不含市值增加、業種、英文名（那些來源被擋）。lite=True。
被擋/抓不到時保留前次 data_jp.json，不覆蓋、不崩潰。
"""
import os, json, time, re, datetime as dt, urllib.request, urllib.parse

RETAIN_DAYS = 60
GAIN_N, TURN_N, LOSE_N = 20, 20, 10
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
YB = "https://finance.yahoo.co.jp"
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"
# (Yahoo symbol 候選, 顯示名, 前端key)；TOPIX 代號不確定 → 多個候選
INDEXES = [(["^N225"], "日經225", "N225"),
           (["^TPX", "998405.T", "^TOPX", "1605.T"], "TOPIX", "TOPIX")]
MK = {"東証PRM": "主板", "東証STD": "標準", "東証GRT": "成長", "東証P": "主板", "東証S": "標準", "東証G": "成長",
      "名証PRM": "主板", "名証MN": "標準", "名証NX": "成長", "札証": "札證", "福証": "福證", "東証ETF": "ETF"}


def _http(url, timeout=30, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "text/html,application/json,*/*",
                "Accept-Language": "ja,en;q=0.8"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(min(2.5 * (i + 1), 12))
    raise last


def _f(x):
    if x is None: return None
    s = str(x).replace(",", "").replace("＋", "+").replace("－", "-").replace("%", "").replace("％", "").strip()
    if s in ("", "-", "--", "N/A"): return None
    try: return float(s)
    except ValueError: return None


def mlabel(m):
    return MK.get((m or "").strip(), (m or "其他").strip() or "其他")


def jpname(s):
    """日文社名精簡：去掉株式会社符號與控股／集團字樣（使用者要求不顯示 holding/会社）。"""
    s = (s or "").strip()
    for x in ("（株）", "(株)", "㈱", "（同）", "(同)"):
        s = s.replace(x, "")
    s = re.sub(r"ホールディングス|ホールディングＨ|ＨＤ$|ｸﾞﾙｰﾌﾟ$|グループ$", "", s)
    return s.strip("　 ・") or (s or "")


def _preload(html):
    m = re.search(r"__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def yahoo_ranking(slug):
    """漲幅/跌幅排行 → [{code,name,market,price,chg,volume}]。"""
    try:
        data = _preload(_http(f"{YB}/stocks/ranking/{slug}?market=all&term=daily"))
    except Exception:
        return []
    if not data:
        return []
    out = []
    for r in (data.get("mainRankingList") or {}).get("results") or []:
        cpr = ((r.get("rankingResult") or {}).get("changePriceRate")) or {}
        out.append({"code": r.get("stockCode"), "name": (r.get("stockName") or "").strip(),
                    "market": r.get("marketName"), "price": _f(r.get("savePrice")),
                    "chg": _f(cpr.get("changePriceRate")), "volume": _f(cpr.get("volume"))})
    return out


def yahoo_turnover():
    """成交額(売買代金)排行；slug/結構未定 → 嘗試多個 slug，並印出 rankingResult keys 以便定位金額欄位。"""
    for slug in ("tradingValue", "tradingValueHigh", "salesValue", "transactionValue", "depositValue"):
        try:
            data = _preload(_http(f"{YB}/stocks/ranking/{slug}?market=all&term=daily"))
        except Exception:
            continue
        results = (data or {}).get("mainRankingList", {}).get("results") or []
        if not results:
            continue
        print(f"成交額 slug = {slug}；rankingResult keys = {list((results[0].get('rankingResult') or {}).keys())}")
        print(f"  rankingResult sample = {json.dumps(results[0].get('rankingResult'), ensure_ascii=False)[:300]}")
        out = []
        for r in results:
            rr = r.get("rankingResult") or {}
            tv = None
            for v in rr.values():            # 掃描各子物件找金額
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        if re.search(r"代金|value|amount|Value", str(kk), re.I) and _f(vv) is not None:
                            tv = _f(vv)
            cpr = rr.get("changePriceRate") or {}
            out.append({"code": r.get("stockCode"), "name": (r.get("stockName") or "").strip(),
                        "market": r.get("marketName"), "price": _f(r.get("savePrice")),
                        "chg": _f(cpr.get("changePriceRate")), "volume": _f(cpr.get("volume")),
                        "turnover_raw": tv})
        return out, slug
    return [], None


def yahoo_index(symbols):
    """以 Yahoo 圖表 API 取指數 60 日 K 與現值；symbols 為候選清單，回傳第一個成功者。"""
    for sym in symbols:
        try:
            data = json.loads(_http(CHART + urllib.parse.quote(sym) + "?range=4mo&interval=1d"))
        except Exception:
            continue
        res = (((data.get("chart") or {}).get("result")) or [None])[0]
        if not res or not res.get("timestamp"):
            continue
        ts = res["timestamp"]
        q = (((res.get("indicators") or {}).get("quote")) or [{}])[0]
        op, hi, lo, cl, vo = q.get("open"), q.get("high"), q.get("low"), q.get("close"), q.get("volume")
        oh, dates = [], []
        for i in range(len(ts)):
            c = cl[i] if cl and i < len(cl) and cl[i] is not None else None
            if c is None:
                continue
            o = op[i] if op and op[i] is not None else c
            h = hi[i] if hi and hi[i] is not None else c
            l = lo[i] if lo and lo[i] is not None else c
            v = vo[i] if vo and i < len(vo) and vo[i] is not None else 0
            d = dt.datetime.utcfromtimestamp(ts[i] + 9 * 3600).date().isoformat()   # JST
            oh.append([round(o, 2), round(h, 2), round(l, 2), round(c, 2), int(v or 0)])
            dates.append(d)
        if not oh:
            continue
        oh, dates = oh[-60:], dates[-60:]
        # 卡片 chg 一律由 K 線末兩根收盤算（保證卡片==K線；Yahoo meta 的前收偶為區間起點而失真）
        value = oh[-1][3]
        prev = oh[-2][3] if len(oh) >= 2 else None
        chg = round((value / prev - 1) * 100, 2) if prev else None
        print(f"指數 {sym} OK：value={round(value,2)} chg={chg} bars={len(oh)}")
        return {"value": round(value, 2), "chg": chg, "ohlcv": oh, "dates": dates}
    return None


# ---------- 樞紐（依市場別）----------
def pivot(lst):
    agg = {}
    for r in lst:
        a = agg.setdefault(r["sector"], {"sector": r["sector"], "count": 0, "sc": 0.0, "st": 0.0})
        a["count"] += 1; a["sc"] += r["chg"]; a["st"] += r["turnover"]
    out = [{"sector": a["sector"], "count": a["count"], "avg_chg": round(a["sc"] / a["count"], 2),
            "mcap_chg": 0.0, "turnover": round(a["st"], 1)} for a in agg.values()]
    return sorted(out, key=lambda x: x["count"], reverse=True)


# ---------- AI 新聞/分析（Gemini，選填）----------
def _gemini(api, prompt):
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "tools": [{"google_search": {}}],
               "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.5,
                                    "thinkingConfig": {"thinkingBudget": 0}}}
    base = "https://generativelanguage.googleapis.com/v1beta/models/"
    for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
        url = base + model + ":generateContent?key=" + api
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    txt = json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"]
                j = json.loads(txt.strip().strip("`").lstrip("json"))
                if j.get("market_summary") or j.get("news"):
                    return j
            except Exception as e:
                if "429" in str(e):
                    break
                time.sleep(4 * (attempt + 1))
    return None


def _jp_prompt(movers, indices):
    lst = "\n".join(f"{r['sym']} {r['name']} {r['chg']:+.1f}%（{r['sector']}）" for r in movers)
    idx = "、".join(f"{i['name']} {i['chg']:+.2f}%" for i in indices if i.get("chg") is not None)
    return ("你是日股研究員。請用繁體中文，並搜尋中／日／英文新聞，完成：\n"
            "1) market_summary：約 130–170 字，聚焦『驅動昨日日股的事件與原因』——日銀政策／利率、日圓匯率、出口外需、重要財報、產業與政策、外資動向、關鍵個股催化等。**不要複述指數漲跌幅數字，改說明背後成因、市場焦點與資金流向。**\n"
            "2) news：8 則影響今日重點日股標的的新聞，其中『中文來源最多 2 則』（如鉅亨網、經濟日報），其餘可為日文／英文／國際來源（日經、Bloomberg、Reuters）。每則含 title、source、url。"
            "**所有 title 一律輸出繁體中文；原文非中文必須翻譯，source 保留原始來源名稱，url 必須為真實可點擊連結。**\n"
            "3) analysis：對下列『每一檔』都要輸出（key=股票代號，繁體中文，1–2 句，精簡）。"
            "**以實際新聞與產業動態為主**：優先具體催化事件（財報、財測、訂單、併購、新產品、政策、匯率、供應鏈），查不到才用產業趨勢推測並標『推測／可能』。公司用簡稱。務必涵蓋每一檔。\n"
            f"指數：{idx}\n標的（代號 公司 漲跌% 市場）：\n{lst}\n"
            "只輸出 JSON：{\"market_summary\":\"\",\"news\":[],\"analysis\":{}}，不要其他文字。")


def ai_layer(movers, indices):
    api = os.environ.get("GEMINI_API_KEY")
    blank = {"ok": False, "news_summary": "（AI 未啟用：設定 GEMINI_API_KEY 後自動生成）", "news": [], "analysis": {}}
    if not api:
        return blank
    _seen = set(); movers = [m for m in movers if not (m["sym"] in _seen or _seen.add(m["sym"]))]
    j = _gemini(api, _jp_prompt(movers, indices))
    if not j:
        j = _gemini(api, _jp_prompt(movers[:10], indices))
    if j:
        return {"ok": True, "news_summary": j.get("market_summary", ""),
                "news": j.get("news", []), "analysis": j.get("analysis", {})}
    return {**blank, "news_summary": "（AI 暫時忙線，稍後自動重整）"}


def _dedup(rs):
    s, o = set(), []
    for r in rs:
        if r["code"] and r["code"] not in s:
            s.add(r["code"]); o.append(r)
    return o


def main():
    now_tpe = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)
    today = now_tpe.date().isoformat()
    session = (now_tpe.date() - dt.timedelta(days=1)).isoformat()

    gain = _dedup(yahoo_ranking("up"))
    lose = _dedup(yahoo_ranking("down"))
    turn_rows, turn_slug = yahoo_turnover()
    turn = _dedup(turn_rows)
    print(f"Yahoo 排行：漲{len(gain)} 跌{len(lose)} 成交額{len(turn)}(slug={turn_slug})")
    if len(gain) < 3:
        print("漲幅排行抓不到（疑似來源異常），保留前次 data_jp.json，本次不更新、不呼叫 AI。")
        return

    def mkrow(r, tv=None):
        price, chg = r.get("price"), r.get("chg")
        if chg is None:
            chg = 0.0
        if tv is None:
            tv = round((price or 0) * (r.get("volume") or 0) / 1e8, 1)   # price×量→億円
        return {"sym": r["code"], "name": jpname(r["name"]), "sector": mlabel(r.get("market")),
                "price": round(price, 2) if price else 0.0, "chg": round(chg, 2),
                "turnover": tv, "mcap": None, "mcap_chg": None}

    gainers = [mkrow(r) for r in gain[:GAIN_N]]
    losers = [mkrow(r) for r in lose[:LOSE_N]]
    # 売買代金原值為円 → 換算億円（÷1e8）；缺值則退回 price×量
    turnover = [mkrow(r, round((r.get("turnover_raw") or 0) / 1e8, 1) if r.get("turnover_raw") else None)
                for r in turn[:TURN_N]]

    # 指數 + K線
    idx_row, idx_hist, axis = [], {}, []
    for syms, name, key in INDEXES:
        ix = yahoo_index(syms)
        if ix:
            idx_row.append({"key": key, "name": name, "value": ix["value"], "chg": ix["chg"]})
            idx_hist[key] = {"name": name, "ohlcv": ix["ohlcv"]}
            if not axis:
                axis = ix["dates"]
        time.sleep(0.2)

    ai = ai_layer(turnover[:5] + gainers[:5] + gainers + turnover + losers, idx_row)

    try:
        bundle = json.load(open("data_jp.json"))
    except Exception:
        bundle = {"symbols": {}, "indices_history": {}, "reports": {}, "dates": [], "streak3": {}, "axis": []}

    if not ai.get("ok") and today in bundle.get("reports", {}):
        _prev = bundle["reports"][today]
        if _prev.get("news"):
            ai = {"ok": False, "news_summary": _prev.get("news_summary", ""),
                  "news": _prev.get("news", []), "analysis": _prev.get("analysis", {})}
    for n in ai.get("news", []):
        if not str(n.get("url", "")).startswith("http"):
            n["url"] = ("https://news.google.com/search?q=" + urllib.parse.quote(n.get("title", "")) +
                        "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
    for r in gainers + turnover + losers:
        r["analysis"] = ai["analysis"].get(r["sym"], "")

    dates_sorted = sorted(set(bundle.get("dates", []) + [today]), reverse=True)[:RETAIN_DAYS]
    bundle["reports"][today] = {
        "us_session": session, "market": "JP", "lite": True,
        "indices": idx_row,
        "news_summary": ai["news_summary"], "news": ai["news"],
        "gainers": gainers, "turnover": turnover, "losers": losers,
        "mcap_up": [], "mcap_down": [],
        "pivot_up": pivot(gainers), "pivot_turnover": pivot(turnover), "pivot_down": pivot(losers),
        "pivot_up_mcap": [], "pivot_down_mcap": []}
    bundle["dates"] = dates_sorted
    bundle["reports"] = {d: bundle["reports"][d] for d in dates_sorted if d in bundle["reports"]}
    if idx_hist:
        bundle["indices_history"] = idx_hist
        bundle["axis"] = axis
    bundle["streak3"] = {today: []}
    bundle["generated_at"] = now_tpe.strftime("%Y-%m-%d %H:%M") + " (台北)"
    bundle["color_convention"] = "INTL"
    bundle["market"] = "JP"
    bundle["lite"] = True
    json.dump(bundle, open("data_jp.json", "w"), ensure_ascii=False)
    print(f"完成 {today}：漲{len(gainers)} 成交{len(turnover)} 跌{len(losers)} 指數{len(idx_row)} 新聞{len(ai['news'])}")


if __name__ == "__main__":
    main()
