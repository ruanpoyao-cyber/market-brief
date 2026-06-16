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
import os, io, csv, json, time, datetime as dt, urllib.request, urllib.parse

RETAIN_DAYS = 60
TOP_N, BOT_N = 30, 10                       # 漲幅/市值增加/成交金額取 30；跌幅/市值減少取 10
MIN_PRICE, MIN_MCAP_USD, MIN_VOL = 5, 3e8, 3e5
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# (Stooq 代號, 顯示名稱, 前端 key) — 前端 key 沿用原 schema
INDEXES = [("^dji", "道瓊工業", "DJI"), ("^spx", "標普500", "GSPC"),
           ("^ndq", "那斯達克", "IXIC"), ("^sox", "費城半導體", "SOX"),
           ("^vix", "VIX 波動率", "VIX")]


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


# ---------- 60 日 K 線（Stooq，免金鑰）----------
def _stooq(code):
    url = "https://stooq.com/q/d/l/?" + urllib.parse.urlencode({"s": code, "i": "d"})
    txt = _http(url, timeout=30).decode("utf-8", "ignore")
    rdr = list(csv.DictReader(io.StringIO(txt)))
    bars = [b for b in rdr if b.get("Close") not in (None, "", "N/D")][-60:]
    oh, dates = [], []
    for b in bars:
        try:
            oh.append([round(float(b["Open"]), 2), round(float(b["High"]), 2),
                       round(float(b["Low"]), 2), round(float(b["Close"]), 2),
                       int(float(b.get("Volume") or 0))])
            dates.append(b["Date"])
        except (ValueError, KeyError):
            continue
    return oh, dates


def history_60d(symbol):
    try:
        return _stooq(symbol.lower() + ".us")
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
def ai_layer(movers, indices):
    api = os.environ.get("GEMINI_API_KEY")
    blank = {"news_summary": "（AI 未啟用：設定 GEMINI_API_KEY 後自動生成）", "news": [], "analysis": {}}
    if not api: return blank
    lst = "\n".join(f"{r['sym']} {r['name']} {r['chg']:+.1f}% ({r['sector']})" for r in movers)
    idx = "、".join(f"{i['name']} {i['chg']:+.2f}%" for i in indices)
    prompt = ("你是美股研究員。請用繁體中文，並搜尋中英文新聞，完成：\n"
              "1) market_summary：120 字內昨夜美股盤後重點。\n"
              "2) news：6 則影響今日重點標的的新聞。每則含 title、source、url。"
              "**所有 title 一律輸出繁體中文；若原文為英文必須翻譯，source 保留原始來源名稱，"
              "url 必須為真實可點擊的原始新聞連結。**\n"
              "3) analysis：對下列每檔一句話說明漲跌/爆量主因 (key=代號，繁體中文)。\n"
              f"指數：{idx}\n標的：\n{lst}\n"
              "只輸出 JSON：{\"market_summary\":\"\",\"news\":[],\"analysis\":{}}，不要其他文字。")
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]}).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api}"
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            txt = json.loads(r.read())["candidates"][0]["content"]["parts"][0]["text"]
        j = json.loads(txt.strip().strip("`").lstrip("json"))
        return {"news_summary": j.get("market_summary", ""), "news": j.get("news", []), "analysis": j.get("analysis", {})}
    except Exception as e:
        return {**blank, "news_summary": f"（AI 呼叫失敗：{e}）"}


def main():
    today = dt.date.today().isoformat()
    us_session = (dt.date.today() - dt.timedelta(days=1)).isoformat()

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
    for code, nm, key in INDEXES:
        oh, dates = _stooq(code) if False else history_index(code)
        if oh:
            bundle["indices_history"][key] = {"name": nm, "ohlcv": oh}
            if key == "GSPC": bundle["axis"] = dates
            c, p = oh[-1][3], oh[-2][3]
            idx_row.append({"key": key, "name": nm, "value": round(c, 2), "chg": round((c / p - 1) * 100, 2)})
        time.sleep(0.2)

    ai = ai_layer(gainers[:12] + turnover[:8] + losers, idx_row)
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
    bundle["generated_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    bundle["color_convention"] = bundle.get("color_convention", "INTL")
    json.dump(bundle, open("data.json", "w"), ensure_ascii=False)
    print(f"完成 {today}：漲{len(gainers)} 市值增{len(mcap_up)} 成交{len(turnover)} "
          f"跌{len(losers)} 市值減{len(mcap_dn)} 連續{len(streaks)} 指數{len(idx_row)}")


def history_index(code):
    """指數 60 日 K（Stooq，代號帶 ^，不加 .us）。"""
    try:
        return _stooq(code)
    except Exception:
        return [], []


if __name__ == "__main__":
    main()
