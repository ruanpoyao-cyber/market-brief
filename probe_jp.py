# -*- coding: utf-8 -*-
"""逆向 Yahoo Finance Japan 排行頁的原始 HTML 結構（在 Actions 雲端執行，log 可看完整內容）。"""
import urllib.request, re, json

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read().decode("utf-8", "ignore")


def main():
    url = "https://finance.yahoo.co.jp/stocks/ranking/up?market=all&term=daily"
    html = fetch(url)
    print("len", len(html))

    # 1) 是否有 __PRELOADED_STATE__ 並可 json.loads
    m = re.search(r"__PRELOADED_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", html, re.S)
    if not m:
        m = re.search(r"__PRELOADED_STATE__\s*=\s*(\{.*)", html, re.S)
    if m:
        blob = m.group(1)
        # 嘗試逐步擴大括號找可解析的 JSON
        try:
            data = json.loads(blob)
            print("PRELOADED json OK, top keys:", list(data.keys())[:20])

            def walk(o, path, depth):
                if depth > 7 or not isinstance(o, (dict, list)):
                    return
                if isinstance(o, list):
                    if len(o) >= 5 and isinstance(o[0], dict):
                        print(f"  ARRAY {path} len={len(o)} keys={list(o[0].keys())[:14]}")
                        print(f"    sample0={json.dumps(o[0], ensure_ascii=False)[:300]}")
                    for i, v in enumerate(o[:1]):
                        walk(v, f"{path}[{i}]", depth + 1)
                else:
                    for k, v in o.items():
                        walk(v, f"{path}.{k}", depth + 1)
            walk(data, "PS", 0)
        except Exception as e:
            print("PRELOADED json parse fail:", e, "| blob head:", blob[:120])
    else:
        print("no __PRELOADED_STATE__")

    # 2) 退而求其次：看 /quote/CODE.T 周邊原始 markup
    i = html.find("/quote/")
    if i >= 0:
        print("--- markup around first /quote/ ---")
        print(html[i - 30:i + 400])


if __name__ == "__main__":
    main()
