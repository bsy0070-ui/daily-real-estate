import os
import json
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright

def get_kr_proxies():
    proxies = []
    try:
        url = "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=KR&ssl=all&anonymity=all"
        res = requests.get(url, timeout=10)
        for line in res.text.splitlines():
            line = line.strip()
            if line:
                proxies.append(f"http://{line}")
    except Exception as e:
        print("프록시 목록 조회 실패:", e)
    return proxies[:15]

def send_kakao(link_url, count):
    rest_key = os.environ["KAKAO_REST_KEY"]
    refresh_token = os.environ["KAKAO_REFRESH_TOKEN"]

    token_res = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": rest_key,
            "refresh_token": refresh_token,
        },
    ).json()
    access_token = token_res["access_token"]

    today_str = datetime.now().strftime("%Y-%m-%d")
    template = {
        "object_type": "text",
        "text": f"🏢 [{today_str}] 새솔동 상가 월세 매물표 도착!\n총 {count}건이 수집되었습니다.",
        "link": {"web_url": link_url, "mobile_web_url": link_url},
        "button_title": "오늘의 매물표 열기",
    }
    res = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
    )
    print("카카오톡 발송 결과:", res.status_code, res.text)

def fetch_listings_with_proxy(proxy_server):
    items = []
    target_url = (
        "https://m.land.naver.com/cluster/ajax/articleList?"
        "rletTpCd=D02&tradTpCd=B2&z=15&lat=37.2835&lon=126.8242"
        "&btm=37.2700&lft=126.8050&top=37.2970&rgt=126.8430"
        "&cortarNo=4159026200&sort=rank"
    )
    with sync_playwright() as p:
        launch_args = {"headless": True}
        if proxy_server:
            launch_args["proxy"] = {"server": proxy_server}
        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            locale="ko-KR",
        )
        page = context.new_page()
        page.goto("https://m.land.naver.com/", timeout=20000)
        page.wait_for_timeout(1500)

        for page_num in range(1, 15):
            res = page.evaluate(
                f"""async () => {{
                    const r = await fetch("{target_url}&page={page_num}");
                    return await r.json();
                }}"""
            )
            body = res.get("body", [])
            if not body:
                break
            for b in body:
                if b.get("cortarNm") == "새솔동" or "새솔" in b.get("cortarNm", ""):
                    items.append(b)
            if not res.get("more", False):
                break
        browser.close()
    return items

def main():
    items = []
    proxies = get_kr_proxies()
    print(f"탐색된 한국 프록시 수: {len(proxies)}")

    for px in proxies:
        try:
            print(f"프록시 시도 중: {px}")
            items = fetch_listings_with_proxy(px)
            if items:
                print(f"수집 성공! 총 {len(items)}건")
                break
        except Exception as e:
            print(f"프록시 실패 ({px}): {e}")
            continue

    os.makedirs("docs", exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    rows_html = ""
    for idx, it in enumerate(items, 1):
        prc = it.get("prc", "-")
        rent = it.get("rentPrc", "-")
        spc1 = it.get("spc1", "-")
        spc2 = it.get("spc2", "-")
        flr = it.get("flrInfo", "-")
        desc = it.get("atclFetrDesc", "")
        atcl_no = it.get("atclNo", "")
        link = f"https://m.land.naver.com/article/info/{atcl_no}"
        rows_html += f"""
        <tr>
            <td>{idx}</td>
            <td><b>{prc} / {rent}</b></td>
            <td>{spc2}㎡ ({spc1}㎡)</td>
            <td>{flr}층</td>
            <td>{desc}</td>
            <td><a href="{link}" target="_blank">보기</a></td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{today_str} 새솔동 상가 월세</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 12px; background: #f8f9fa; }}
  h2 {{ font-size: 18px; margin-bottom: 10px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; font-size: 13px; }}
  th, td {{ border: 1px solid #dee2e6; padding: 8px 6px; text-align: left; }}
  th {{ background: #212529; color: #fff; position: sticky; top: 0; }}
  tr:nth-child(even) {{ background: #f1f3f5; }}
  a {{ color: #0d6efd; text-decoration: none; font-weight: bold; }}
</style>
</head>
<body>
  <h2>🏢 {today_str} 새솔동 상가 월세 (총 {len(items)}건)</h2>
  <table>
    <thead>
      <tr><th>No</th><th>보증금/월세</th><th>전용(계약)</th><th>층</th><th>특징</th><th>링크</th></tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
</body>
</html>"""

    with open("docs/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    actor = os.environ.get("GITHUB_ACTOR", "bsy0070-ui")
    repo = os.environ.get("GITHUB_REPO", "daily-real-estate")
    page_url = f"https://{actor}.github.io/{repo}/"
    send_kakao(page_url, len(items))

if __name__ == "__main__":
    main()
