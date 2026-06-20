#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股盤後晨報 — 正式版資料產生器（Keyless 全市場）。輸出 data.json（與 index.html 同 schema）。

資料來源（皆免金鑰）：
  - 全市場報價＋市值＋族群：Nasdaq 公開 screener（api.nasdaq.com）。一次撈全市場。
  - 60 日 K 線（個股與指數）：Stooq（stooq.com，對雲端 IP 友善）。
新聞摘要／分析／翻譯：Gemini（需 GEMINI_API_KEY，選填）。

環境變數：GEMINI_API_KEY（選填）。本版不需要任何行情金鑰。
排程：台北 06:30 = UTC 22:30 前一日 → GitHub Actions cron "30 22 * * 1-5"。
資料僅供研究參考，非投資建議。
"""
import os, io, csv, json, time, re, datetime as dt, urllib.request, urllib.parse

RETAIN_DAYS = 60
TOP_N, BOT_N = 30, 10                       # 漲幅/市值增加/成交金額取 30；跌幅/市值減少取 10
MIN_PRICE, MIN_MCAP_USD, MIN_VOL = 5, 3e8, 3e5
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# (Nasdaq 指數代號, 顯示名稱, 前端 key) — 前端 key 沿用原 schema
INDEXES = [("DJI", "道瓊工業", "DJI"), ("SPX", "標普500", "GSPC"),
           ("COMP", "那斯達克", "IXIC"), ("SOX", "費城半導體", "SOX"),
           ("VIX", "VIX 波動率", "VIX")]

# 各指數 60 日 K 線來源：道瓊/標普用 ETF 代理(DIA/SPY)並校準到真實點位、
# VIX 用 Cboe 官方 CSV、那斯達克(COMP)/費半(SOX)用 Nasdaq 指數歷史。
HIST_SRC = {"DJI": ("etf_proxy", "DIA"), "GSPC": ("etf_proxy", "SPY"),
            "IXIC": ("nasdaq_index", "COMP"), "SOX": ("nasdaq_index", "SOX"),
            "VIX": ("cboe", "VIX")}


def _http(url, headers=None, timeout=40, tries=3):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers: h.update(headers)
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e; time.sleep(1.5 * (i + 1))
    raise last


# ---------- 全市場快照（Nasdaq screener，免金鑰）----------
def _f(x):
    """把 '$1,234.50' / '1.23%' / '12,345' 轉成 float；無法解析回 None。"""
    if x is None: return None
    s = str(x).replace("$", "").replace(",", "").replace("%", "").strip()
    if s in ("", "N/A", "--"): return None
    try: return float(s)
    except ValueError: return None


def market_snapshot():
    url = ("https://api.nasdaq.com/api/screener/stocks"
           "?tableonly=true&limit=9000&offset=0&download=true")
    raw = _http(url, headers={"Accept": "application/json",
                              "Accept-Language": "en-US,en;q=0.9",
                              "Origin": "https://www.nasdaq.com",
                              "Referer": "https://www.nasdaq.com/"})
    j = json.loads(raw.decode())
    data = j.get("data") or {}
    rows = data.get("rows")
    if not rows:
        rows = (data.get("table") or {}).get("rows") or []
    out = []
    for q in rows:
        sym = (q.get("symbol") or "").strip()
        if not sym or "^" in sym or "/" in sym: continue
        price = _f(q.get("lastsale"))
        net   = _f(q.get("netchange"))
        pct   = _f(q.get("pctchange"))
        mcap  = _f(q.get("marketCap"))
        vol   = _f(q.get("volume")) or 0
        if price is None or mcap is None: continue
        if net is None and pct is None: continue
        if net is None: net = price - price / (1 + pct / 100) if pct not in (None, -100) else 0
        prev = price - net
        if pct is None: pct = (price / prev - 1) * 100 if prev else 0
        if price < MIN_PRICE or mcap < MIN_MCAP_USD or vol < MIN_VOL: continue
        shares = mcap / price
        out.append({
            "sym": sym,
            "name": q.get("name") or sym,
            "price": round(price, 2),
            "chg": round(pct, 2),
            "mcap": round(mcap / 1e8, 1),                  # 市值（億美元）
            "mcap_chg": round(shares * net / 1e8, 1),      # 市值增減（億美元）
            "turnover": round(price * vol / 1e8, 1),       # 成交金額（億美元）
            "sector": (q.get("sector") or "其他").strip() or "其他",
        })
    return out


# ---------- 60 日 K 線（Nasdaq historical，免金鑰；與 screener 同主機）----------
def _nasdaq_hist(symbol, assetclass):
    today = dt.date.today()
    frm = (today - dt.timedelta(days=130)).isoformat()
    url = ("https://api.nasdaq.com/api/quote/" + urllib.parse.quote(symbol) +
           "/historical?assetclass=" + assetclass +
           "&fromdate=" + frm + "&todate=" + today.isoformat() + "&limit=70")
    raw = _http(url, headers={"Accept": "application/json",
                              "Accept-Language": "en-US,en;q=0.9",
                              "Origin": "https://www.nasdaq.com",
                              "Referer": "https://www.nasdaq.com/"})
    j = json.loads(raw.decode())
    data = j.get("data") or {}
    rows = ((data.get("tradesTable") or {}).get("rows")) or []
    bars = list(reversed(rows))[-60:]               # 由舊到新
    oh, dates = [], []
    for b in bars:
        c = _f(b.get("close"))
        if c is None:
            continue
        o = _f(b.get("open")) or c
        h = _f(b.get("high")) or c
        l = _f(b.get("low")) or c
        v = _f(b.get("volume")) or 0
        d = str(b.get("date") or "")
        try:
            mm, dd, yy = d.split("/"); ds = f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}"
        except ValueError:
            ds = d
        oh.append([round(o, 2), round(h, 2), round(l, 2), round(c, 2), int(v)])
        dates.append(ds)
    return oh, dates


def history_60d(symbol):
    try:
        return _nasdaq_hist(symbol, "stocks")
    except Exception:
        return [], []


# ---------- 樞紐 ----------
def pivot(lst):
    agg = {}
    for r in lst:
        a = agg.setdefault(r["sector"], {"sector": r["sector"], "count": 0, "sc": 0.0, "sm": 0.0, "st": 0.0})
        a["count"] += 1; a["sc"] += r["chg"]; a["sm"] += r["mcap_chg"]; a["st"] += r["turnover"]
    out = [{"sector": a["sector"], "count": a["count"], "avg_chg": round(a["sc"] / a["count"], 2),
            "mcap_chg": round(a["sm"], 1), "turnover": round(a["st"], 1)} for a in agg.values()]
    return sorted(out, key=lambda x: x["count"], reverse=True)


# ---------- AI 新聞/分析/翻譯（Gemini，選填）----------
def _short_name(n):
    """公司簡名：去除股權類型字樣與公司形式字（Apple Inc. Common Stock → Apple）。"""
    s = str(n or "")
    s = re.sub(r"\bAmerican\s+Depositary\s+(Shares|Receipts)\b", "", s, flags=re.I)
    s = re.sub(r"\b(Depositary|Depository)\s+(Shares|Receipts)\b", "", s, flags=re.I)
    s = re.sub(r"\s+(Common|Ordinary|Capital|Preferred)\s+(Stock|Shares|Share)\b", "", s, flags=re.I)
    s = re.sub(r"\s+Class\s+[A-Z]\b", "", s, flags=re.I)
    s = re.sub(r"\s+(each\s+)?representing\b.*$", "", s, flags=re.I)
    s = re.sub(r"\.com\b", "", s, flags=re.I)
    prev = None
    while prev != s and s:
        prev = s
        s = re.sub(r"[\s,]+(Incorporated|Corporation|Company|Limited|Holdings|Technologies|Technology|Group|Corp|Inc|Co|Ltd|PLC|LLC|LP|N\.V\.|S\.A\.)\.?\s*$", "", s, flags=re.I).strip()
    return re.sub(r"\s{2,}", " ", s).strip()


def ai_layer(movers, indices):
    api = os.environ.get("GEMINI_API_KEY")
    blank = {"ok": False, "news_summary": "（AI 未啟用：設定 GEMINI_API_KEY 後自動生成）", "news": [], "analysis": {}}
    if not api: return blank
    _seen = set(); movers = [m for m in movers if not (m["sym"] in _seen or _seen.add(m["sym"]))]  # 去重：同一檔只查一次
    lst = "\n".join(f"{r['sym']} {_short_name(r['name'])} {r['chg']:+.1f}% ({r['sector']})" for r in movers)
    idx = "、".join(f"{i['name']} {i['chg']:+.2f}%" for i in indices)
    prompt = ("你是美股研究員。請用繁體中文，並搜尋中英文新聞，完成：\n"
              "1) market_summary：約 130–170 字，聚焦『驅動昨夜美股的事件與原因』——例如聯準會/利率、地緣政治、重要財報、產業與政策消息、資金輪動、關鍵個股催化等。**不要複述各指數的漲跌幅數字（使用者已從上方卡片看到），改說明背後成因、市場焦點與資金流向。**\n"
              "2) news：8 則影響今日重點標的的新聞，其中『中文來源最多 2 則』（如鉅亨網、經濟日報、永豐金證券），其餘須為英文／國際來源（如 Reuters、Bloomberg、CNBC 等）。每則含 title、source、url。"
              "**所有 title 一律輸出繁體中文；若原文為英文必須翻譯，source 保留原始來源名稱，"
              "url 必須為真實可點擊的原始新聞連結。**\n"
              "3) analysis：對下列『每一檔』都要輸出（key=股票代號，繁體中文，1–2 句，精簡不冗詞）。"
              "**以實際新聞與產業動態為主**：優先點出具體催化事件（財報數字、財測、分析師評等調整、訂單／產能、併購、新產品、政策與供應鏈消息等），能講出來源或具體事實就講。"
              "**只有在查不到明確消息時才用產業趨勢推測，且須以『推測』『可能』等字眼標明，避免通篇臆測。**"
              "**公司一律以簡稱表示（如 Apple、NVIDIA、Micron），不要寫出 Inc./Corporation/Common Stock 等字樣。** 務必涵蓋清單中每一檔。\n"
              f"指數：{idx}\n標的：\n{lst}\n"
              "只輸出 JSON：{\"market_summary\":\"\",\"news\":[],\"analysis\":{}}，不要其他文字。")
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "tools": [{"google_search": {}}],
               "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.5,
                                    "thinkingConfig": {"thinkingBudget": 0}}}
    base = "https://generativelanguage.googleapis.com/v1beta/models/"
    last_err = None
    for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):   # 主模型(品質)→失敗換輕量版(額度較高、獨立計)；各只試一次，省配額又較不易撞上限
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
            last_err = "空回應"
        except Exception as e:
            last_err = e                                     # 含 429/忙線：直接換下一個模型試一次（額度獨立）
    return {**blank, "news_summary": f"（AI 暫時忙線：{last_err}）"}


def main():
    now_tpe = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)   # 台北時間（避免 UTC 造成日期晚一天）
    today = now_tpe.date().isoformat()
    us_session = (now_tpe.date() - dt.timedelta(days=1)).isoformat()

    rows = market_snapshot()
    print(f"全市場符合條件標的：{len(rows)}")
    gainers  = sorted(rows, key=lambda r: r["chg"], reverse=True)[:TOP_N]
    mcap_up  = sorted(rows, key=lambda r: r["mcap_chg"], reverse=True)[:TOP_N]
    turnover = sorted(rows, key=lambda r: r["turnover"], reverse=True)[:TOP_N]
    losers   = sorted(rows, key=lambda r: r["chg"])[:BOT_N]
    mcap_dn  = sorted(rows, key=lambda r: r["mcap_chg"])[:BOT_N]

    try: bundle = json.load(open("data.json"))
    except Exception:
        bundle = {"symbols": {}, "indices_history": {}, "reports": {}, "dates": [], "streak3": {}, "axis": []}

    idx_row = []
    cnbc = cnbc_indices()                                   # 五大指數即時報價（含道瓊/標普/VIX）
    for code, nm, key in INDEXES:
        v = c = None
        if key in cnbc:
            v, c = cnbc[key]
        src, sym = HIST_SRC[key]                            # 各指數 K 線來源
        if src == "nasdaq_index":
            oh, dates = history_index(sym)
        elif src == "cboe":
            oh, dates = cboe_history(sym)
        elif src == "etf_proxy":
            oh, dates = history_etf(sym)
            if oh and v:                                   # ETF 走勢校準到真實指數點位
                k = v / oh[-1][3]
                oh = [[round(o * k, 2), round(h * k, 2), round(l * k, 2), round(cl * k, 2), vol]
                      for (o, h, l, cl, vol) in oh]
        else:
            oh, dates = [], []
        if oh:
            bundle["indices_history"][key] = {"name": nm, "ohlcv": oh}
            if not bundle.get("axis"):
                bundle["axis"] = dates
            if v is None:                                  # 無 CNBC 值時用歷史末值
                cc, pp = oh[-1][3], oh[-2][3]
                v, c = round(cc, 2), round((cc / pp - 1) * 100, 2)
        if v is not None:
            idx_row.append({"key": key, "name": nm, "value": v, "chg": c})
        time.sleep(0.2)

    fg = fear_greed()                                      # CNN 恐懼貪婪指數（VIX 旁邊那張卡）
    if fg:
        rmap = {"extreme fear": "極度恐懼", "fear": "恐懼", "neutral": "中性",
                "greed": "貪婪", "extreme greed": "極度貪婪"}
        idx_row.append({"key": "FGI", "name": "恐懼貪婪", "value": fg["score"],
                        "chg": fg["chg"], "rating": rmap.get(fg["rating"], fg["rating"])})
        if fg["ohlcv"]:
            bundle["indices_history"]["FGI"] = {"name": "恐懼貪婪", "ohlcv": fg["ohlcv"]}

    ai = ai_layer(gainers[:10] + turnover[:10] + mcap_up[:10] + losers, idx_row)
    if not ai.get("ok"):                              # 重試清單全失敗 → 沿用既有成功新聞，頁面不空白
        _cand = ([today] if today in bundle.get("reports", {}) else [])
        _cand += sorted([x for x in bundle.get("reports", {}) if x != today], reverse=True)
        for _d in _cand:
            _prev = bundle["reports"][_d]
            if _prev.get("news"):
                if _d == today:                        # 沿用今天稍早成功的新聞與分析
                    ai = {"ok": False, "news_summary": _prev.get("news_summary", ""),
                          "news": _prev.get("news", []), "analysis": _prev.get("analysis", {})}
                else:                                  # 沿用前一交易日（標示清楚）
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

    for r in {x["sym"]: x for x in gainers + mcap_up + turnover + losers + mcap_dn}.values():
        oh, _ = history_60d(r["sym"])
        if oh: bundle["symbols"][r["sym"]] = {"name": r["name"], "sector": r["sector"], "ohlcv": oh}
        time.sleep(0.2)

    dates_sorted = sorted(set(bundle["dates"] + [today]), reverse=True)[:RETAIN_DAYS]
    last3 = dates_sorted[:3]
    def in_list(d, key, sym): return any(x["sym"] == sym for x in bundle["reports"].get(d, {}).get(key, []))
    streaks = []
    if len(last3) == 3:
        for r in gainers + mcap_up:
            g = all((d == today and any(x["sym"] == r["sym"] for x in gainers)) or in_list(d, "gainers", r["sym"]) for d in last3)
            m = all((d == today and any(x["sym"] == r["sym"] for x in mcap_up)) or in_list(d, "mcap_up", r["sym"]) for d in last3)
            if (g or m) and not any(s["sym"] == r["sym"] for s in streaks):
                streaks.append({"sym": r["sym"], "name": r["name"], "sector": r["sector"],
                                "tags": (["漲幅前30"] if g else []) + (["市值增加前30"] if m else [])})

    bundle["reports"][today] = {"us_session": us_session, "indices": idx_row,
        "news_summary": ai["news_summary"], "news": ai["news"],
        "gainers": gainers, "mcap_up": mcap_up, "turnover": turnover, "losers": losers, "mcap_down": mcap_dn,
        "pivot_up": pivot(gainers), "pivot_up_mcap": pivot(mcap_up), "pivot_turnover": pivot(turnover),
        "pivot_down": pivot(losers), "pivot_down_mcap": pivot(mcap_dn)}
    bundle["dates"] = dates_sorted
    bundle["reports"] = {d: bundle["reports"][d] for d in dates_sorted if d in bundle["reports"]}
    bundle["streak3"] = {today: streaks}
    bundle["generated_at"] = now_tpe.strftime("%Y-%m-%d %H:%M") + " (台北)"
    bundle["color_convention"] = bundle.get("color_convention", "INTL")
    json.dump(bundle, open("data.json", "w"), ensure_ascii=False)
    print(f"完成 {today}：漲{len(gainers)} 市值增{len(mcap_up)} 成交{len(turnover)} "
          f"跌{len(losers)} 市值減{len(mcap_dn)} 連續{len(streaks)} 指數{len(idx_row)}")


def cnbc_indices():
    """道瓊/標普/那斯達克/費半/VIX 即時報價（CNBC，免金鑰，含 Nasdaq 取不到的指數）。
    回傳 {前端key: (value, chg)}。"""
    sym2key = {".DJI": "DJI", ".SPX": "GSPC", ".IXIC": "IXIC", ".SOX": "SOX", ".VIX": "VIX"}
    syms = urllib.parse.quote("|".join(sym2key.keys()), safe="")
    url = ("https://quote.cnbc.com/quote-html-webservice/quote.htm?symbols=" + syms +
           "&requestMethod=quick&output=json")
    out = {}
    try:
        raw = _http(url, headers={"Accept": "application/json"})
        arr = (((json.loads(raw.decode()).get("QuickQuoteResult") or {}).get("QuickQuote")) or [])
        if isinstance(arr, dict):
            arr = [arr]
        for it in arr:
            k = sym2key.get(it.get("symbol"))
            if not k:
                continue
            v = _f(it.get("last"))
            c = _f(it.get("change_pct"))            # CNBC 已是百分比數值（無 % 號）
            if v is not None:
                out[k] = (round(v, 2), round(c, 2) if c is not None else 0.0)
    except Exception:
        pass
    return out


def fear_greed():
    """CNN 恐懼貪婪指數（公開 dataviz API，免金鑰）。回傳 dict 或 None。"""
    try:
        raw = _http("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                    headers={"Accept": "application/json",
                             "Accept-Language": "en-US,en;q=0.9",
                             "Origin": "https://edition.cnn.com",
                             "Referer": "https://edition.cnn.com/markets/fear-and-greed"},
                    timeout=30)
        j = json.loads(raw.decode())
        fg = j.get("fear_and_greed") or {}
        score = fg.get("score")
        if score is None:
            return None
        prev = fg.get("previous_close")
        hist = ((j.get("fear_and_greed_historical") or {}).get("data")) or []
        pts = []
        for d in hist:
            y, x = d.get("y"), d.get("x")
            if y is None or x is None:
                continue
            ds = dt.datetime.fromtimestamp(x / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
            pts.append((ds, float(y)))
        oh, dates = [], []
        for i in range(1, len(pts)):                       # 用日分數做蠟燭：開=昨、收=今
            o, c = pts[i - 1][1], pts[i][1]
            oh.append([round(o, 1), round(max(o, c), 1), round(min(o, c), 1), round(c, 1), 0])
            dates.append(pts[i][0])
        return {"score": round(float(score)), "rating": fg.get("rating") or "",
                "chg": round(float(score) - float(prev), 1) if prev is not None else 0.0,
                "ohlcv": oh[-60:], "dates": dates[-60:]}
    except Exception:
        return None


def index_quote(code):
    """指數即時報價（Nasdaq info 端點），歷史抓不到時用來補卡片。回傳 (value, chg) 或 None。"""
    try:
        url = "https://api.nasdaq.com/api/quote/" + urllib.parse.quote(code) + "/info?assetclass=index"
        raw = _http(url, headers={"Accept": "application/json",
                                  "Accept-Language": "en-US,en;q=0.9",
                                  "Origin": "https://www.nasdaq.com",
                                  "Referer": "https://www.nasdaq.com/"})
        pd = ((json.loads(raw.decode()).get("data") or {}).get("primaryData")) or {}
        v = _f(pd.get("lastSalePrice"))
        c = _f(pd.get("percentageChange"))
        if v is None:
            return None
        return (round(v, 2), round(c, 2) if c is not None else 0.0)
    except Exception:
        return None


def history_etf(symbol):
    """ETF 60 日 K（Nasdaq historical, assetclass=etf）。供道瓊(DIA)/標普(SPY)代理用。"""
    try:
        return _nasdaq_hist(symbol, "etf")
    except Exception:
        return [], []


def cboe_history(sym):
    """指數官方日線（Cboe 公開 CSV，免金鑰）。回傳 (ohlcv, dates)。VIX 用此來源。"""
    try:
        url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/" + sym + "_History.csv"
        txt = _http(url, timeout=30).decode("utf-8", "ignore")
        rows = list(csv.DictReader(io.StringIO(txt)))[-60:]
        oh, dates = [], []
        for r in rows:
            try:
                o, h, l, c = float(r["OPEN"]), float(r["HIGH"]), float(r["LOW"]), float(r["CLOSE"])
            except (ValueError, KeyError):
                continue
            d = str(r.get("DATE", ""))
            try:
                mm, dd, yy = d.split("/"); ds = f"{yy}-{mm.zfill(2)}-{dd.zfill(2)}"
            except ValueError:
                ds = d
            oh.append([round(o, 2), round(h, 2), round(l, 2), round(c, 2), 0])
            dates.append(ds)
        return oh, dates
    except Exception:
        return [], []


def history_index(code):
    """指數 60 日 K（Nasdaq historical, assetclass=index）。"""
    try:
        return _nasdaq_hist(code, "index")
    except Exception:
        return [], []


if __name__ == "__main__":
    main()
