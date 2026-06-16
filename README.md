# Market Brief — 美股盤後互動晨報

每天台北時間 06:30 自動產出前一個美股交易日的盤後晨報：全市場漲幅 / 市值增加 / 成交金額前 30、跌幅 / 市值減少前 10，依族群做樞紐分析，標的可 hover 看 60 日 K 線（疊量），並可依日期回看每天的報告。新聞摘要與每檔漲跌主因由 Gemini 生成繁體中文。

**線上版**：<https://ruanpoyao-cyber.github.io/market-brief/>

> 命名刻意不綁單一市場。目前先實作美股；之後要加其他市場（日股…）時，各市場用自己的 `build_data_<market>.py` 產生相同 schema 的資料，前端再加市場切換即可，主架構不變。

---

## 檔案結構

| 檔案 | 角色 |
|---|---|
| `index.html` | 互動式前端成品（所有區段、hover K 線、日期切換、族群樞紐）。載入時 `fetch('data.json')`，失敗時退回內嵌樣本。 |
| `build_data_us.py` | 美股資料產生器，輸出 `data.json`。純 Python 標準函式庫，不需安裝套件。 |
| `data.json` | 由 Action 產生並 commit 回 repo，滾動保留最近 60 個報告日。 |
| `.github/workflows/brief.yml` | GitHub Actions：排程 + 手動觸發，跑 `build_data_us.py` 並把 `data.json` commit 回 main。 |

---

## 資料來源（全部免行情金鑰）

| 用途 | 來源 | 備註 |
|---|---|---|
| 全市場報價、市值、族群、漲跌排行 | **Nasdaq 公開 screener** `api.nasdaq.com/api/screener/stocks` | 一次撈全市場（NASDAQ/NYSE/AMEX），含 sector，省掉額外的族群對照表。 |
| 個股 60 日 K 線 | **Nasdaq historical** `api.nasdaq.com/api/quote/{sym}/historical?assetclass=stocks` | 與 screener 同主機，雲端 IP 可用。 |
| 五大指數即時點位（道瓊 / 標普500 / 那斯達克 / 費半 / VIX） | **CNBC quote service** `quote.cnbc.com/quote-html-webservice/quote.htm?symbols=.DJI\|.SPX\|.IXIC\|.SOX\|.VIX&requestMethod=quick&output=json` | 解析 `QuickQuoteResult.QuickQuote` 的 `last` / `change_pct`。道瓊 / 標普 / VIX 是專有指數，Nasdaq 端點取不到，故改用 CNBC。 |
| 指數 60 日 K 線（hover） | Nasdaq historical（assetclass=index） | 只有那斯達克(COMP)、費半(SOX) 有；道瓊 / 標普 / VIX 只有卡片點位、無 K 線（前端遇到無歷史會優雅略過 hover）。 |
| 新聞摘要、每檔漲跌主因、英文標題翻譯 | **Gemini**（`gemini-2.5-flash` + google_search） | 需環境變數 `GEMINI_API_KEY`（選填；未設時新聞區顯示「AI 未啟用」，行情照常）。 |

> 設計上避開了 Stooq / Yahoo —— 它們會擋 GitHub Actions 的雲端資料中心 IP，導致抓取全空。

---

## 頁面區段

1. 日期切換（回看每天報告）
2. 四大指數 + VIX（游標移上看 K 線）
3. 市場重點新聞摘要（約 3 行細節 + 6 則新聞，英文標題一律翻為繁中）
4. 樞紐分析 — 漲幅前30 / 市值增加前30 / 成交金額前30（依族群）
5. 樞紐分析 — 跌幅前10 / 市值減少前10
6. 連續三天上榜標的
7. 成交金額 / 漲幅 / 市值增加前30 表格 + 分析（標的可 hover K 線）
8. 跌幅 / 市值減少前10 表格 + 分析

---

## 部署（GitHub Pages + Actions）

1. repo 設為 **public**，Settings → Pages → Deploy from a branch → `main` / `(root)`。
2. Settings → Secrets and variables → Actions → 新增 `GEMINI_API_KEY`（選填）。
3. Action 排程 `cron: "30 22 * * 1-5"`（UTC 22:30 = 台北 06:30，週一至週五），也可在 Actions 頁手動 **Run workflow**。
4. Action 跑完把 `data.json` commit 回 main，Pages 自動重新部署。

**成本**：主機免費；行情來源免費；Gemini 一天一次，每月台幣數十元等級。

---

## 已知限制

- 道瓊 / 標普500 / VIX 只有即時點位卡片，**沒有 60 日 K 線 hover**（那是道瓊 / S&P / CBOE 的專有指數，免費歷史不可得）。那斯達克與費半則有 K 線。
- 行情數值依各來源更新時點略有差異（screener / historical 為日線收盤，CNBC 指數為即時）。
- 各非官方端點若日後改版或加防爬，需調整抓取邏輯。

---

## 加入新市場（未來）

1. 新增 `build_data_<market>.py`，輸出與美股相同的 `data.json` schema（沿用 `build_data_us.py` 為範本）。
2. 前端加一個市場切換（與日期切換同排）。
3. 各市場各自一份 data，或合併成多市場 bundle。

---

資料僅供研究參考，非投資建議。
