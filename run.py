from datetime import datetime
from itertools import combinations
import json
import os
import re
import time
from playwright.sync_api import sync_playwright

SAVE_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "매일매물")

BUILDING_DB = {}


def match_rooms(bldg_name, floor, area_str, desc):
  try:
    target = float(area_str)
  except (ValueError, TypeError):
    return False, (desc[:45] if desc else "미확정 (상세 면적 확인 필요)")

  matched_key = next(
      (k for k in BUILDING_DB if k in bldg_name or bldg_name in k), None
  )
  if not matched_key or floor not in BUILDING_DB[matched_key]:
    clean_desc = desc.replace(bldg_name, "").strip()
    return False, (
        f"{bldg_name} ({clean_desc[:35]})"
        if clean_desc
        else f"{bldg_name} 미확정"
    )

  rooms = BUILDING_DB[matched_key][floor]
  entries = list(rooms.items())
  tol = 0.25

  singles = [
      f"{matched_key} {n}" for n, a in entries if abs(a - target) <= tol
  ]
  combos = []
  for r_cnt in (2, 3):
    for combo in combinations(entries, r_cnt):
      s = round(sum(x[1] for x in combo), 2)
      d = round(abs(s - target), 2)
      if d <= tol:
        names = " + ".join(x[0] for x in combo)
        areas = " + ".join(str(x[1]) for x in combo)
        combos.append(
            f"{matched_key} {names} ({areas} = {s}㎡, 차이 {d:.2f}㎡)"
        )

  results = []
  if singles:
    results.append(" | ".join(singles))
  if combos:
    results.append("합산 추정: " + " | ".join(combos))

  if results:
    return True, " | ".join(results)
  return False, (
      f"미확정 ({desc[:30]})" if desc else f"{matched_key} {floor}층 후보 없음"
  )


def run():
  print("1. Npay 부동산 접속 -> [화성시 만세구 새솔동 · 상가 · 월세] 수집 시작...")
  articles_list = []
  seen_keys = set()

  with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False, args=["--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    context.add_init_script("""
      Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
      localStorage.setItem('FIN_LAND_COMMUNITY_POPUP_HIDE', '9999999999999');
    """)
    page = context.new_page()

    # 1) 정식 홈 접속 -> 새로운 부동산 보기
    page.goto("https://land.naver.com/", wait_until="domcontentloaded")
    time.sleep(2)
    page.goto("https://fin.land.naver.com/", timeout=60000)
    page.wait_for_timeout(3000)
    time.sleep(3)

    # 2) 팝업 닫기
    for _ in range(3):
      try:
        close_popup = page.locator("text=7일간 보지 않기").first
        if close_popup.is_visible():
          close_popup.click(force=True)
          time.sleep(0.8)
      except Exception:
        pass
      time.sleep(0.4)

    # 3) 상가 전용(D02) + 월세 전용(B2) 모드로 전환
    cur_url = page.url
    shop_url = re.sub(
        r"realEstateTypes=[^&]+", "realEstateTypes=D02", cur_url
    )
    shop_url = re.sub(r"tradeTypes=[^&]+", "tradeTypes=B2", shop_url)
    page.goto(shop_url, wait_until="domcontentloaded")
    time.sleep(3)

    # 4) 상단 검색창 클릭 -> '새솔동' 입력 -> '경기도 화성시 만세구 새솔동' 정확 클릭!
    page.locator("text=단지, 주소, 지하철, 초등학교").first.click(force=True)
    time.sleep(1)
    page.keyboard.type("새솔동", delay=80)
    time.sleep(2)
    page.get_by_text("경기도 화성시 만세구 새솔동", exact=True).first.click(
        force=True
    )
    time.sleep(3.5)

    # 5) 하단 '매물 XXX' 버튼 클릭해 왼쪽 상가 월세 목록 열기
    try:
      pill = page.locator("text=/^매물\\s*\\d+/").first
      if pill.is_visible():
        pill.click(force=True)
        time.sleep(3)
    except Exception:
      pass

    # 6) 왼쪽 매물 목록 스크롤하면서 화면에 렌더링된 상가 월세 카드 텍스트 직접 추출
    page.mouse.move(320, 500)
    for _ in range(25):
      items = page.evaluate("""() => {
        const results = [];
        const candidates = Array.from(document.querySelectorAll('li, div')).filter(el => {
          const rect = el.getBoundingClientRect();
          if (rect.left > 600 || rect.width < 200 || rect.height < 60 || rect.height > 320) return false;
          const t = el.innerText || '';
          return t.includes('월세 ') && t.includes('㎡') && t.includes('층');
        });
        for (const el of candidates) {
          const hasChild = candidates.some(other => other !== el && el.contains(other));
          if (!hasChild) {
            results.push(el.innerText);
          }
        }
        return results;
      }""")

      for raw_txt in items:
        lines = [l.strip() for l in raw_txt.split("\n") if l.strip()]
        if len(lines) < 2:
          continue
        full_txt = " ".join(lines)

        m_price = re.search(r"월세\s*([0-9억만천,\s/]+)", full_txt)
        if not m_price:
          continue
        price = m_price.group(1).strip()

        bldg_name = lines[0]

        m_area = re.search(r"전용\s*([0-9]+(?:\.[0-9]+)?)", full_txt)
        if not m_area:
          m_area = re.search(r"/([0-9]+(?:\.[0-9]+)?)\s*㎡", full_txt)
        area = m_area.group(1) if m_area else ""

        m_floor = re.search(r"([B0-9]+)(?:/[B0-9]+)?층", full_txt)
        floor = m_floor.group(1) if m_floor else ""

        m_date = re.search(r"(?:20)?(\d{2}\.\d{2}\.\d{2})", full_txt)
        confirm_date = (
            m_date.group(1) if m_date else datetime.now().strftime("%y.%m.%d")
        )

        realtor = next(
            (
                l
                for l in reversed(lines)
                if "공인중개사" in l or "부동산" in l
            ),
            "네이버 등록 중개사",
        )

        uniq_key = f"{bldg_name}|{floor}|{price}|{area}|{realtor}"
        if uniq_key in seen_keys:
          continue
        seen_keys.add(uniq_key)

        is_confirmed, room_info = match_rooms(
            bldg_name, floor, area, full_txt
        )
        articles_list.append({
            "building": bldg_name,
            "floor": floor,
            "room_info": room_info,
            "is_confirmed": is_confirmed,
            "trade_type": "월세",
            "price": price,
            "area": area,
            "confirm_date": confirm_date,
            "realtor": realtor,
            "article_no": f"새솔-{len(articles_list)+1:03d}",
        })

      page.mouse.wheel(0, 1200)
      time.sleep(0.7)

    browser.close()

  articles_list.sort(key=lambda x: (x["building"], x["floor"]))
  total_cnt = len(articles_list)
  confirmed_cnt = sum(1 for a in articles_list if a["is_confirmed"])
  unconfirmed_cnt = total_cnt - confirmed_cnt

  today_str = datetime.now().strftime("%Y-%m-%d")
  json_str = json.dumps(articles_list, ensure_ascii=False)

  html = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{today_str} 새솔동 상가 월세 확인 매물표</title>
<style>
  body{{font-family:'Malgun Gothic',sans-serif;margin:20px;color:#222;background:#f8fafc}}
  h1{{font-size:21px;margin:0 0 8px 0}}
  .sub-note{{font-size:11.5px;color:#666;line-height:1.5;margin-bottom:12px}}
  .badges{{margin-bottom:14px}}
  .badge-rent{{background:#b8e994;color:#1e3720;padding:4px 10px;border-radius:4px;font-weight:bold;font-size:12px;margin-right:6px}}
  .summary-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;max-width:650px;margin-bottom:16px}}
  .card{{background:#fff;border:1px solid #dce3ea;border-top:3px solid #1e3746;border-radius:6px;padding:12px 16px}}
  .card.ok{{border-top-color:#27ae60}}
  .card.warn{{border-top-color:#e67e22}}
  .card-title{{font-size:12px;color:#666;margin-bottom:4px}}
  .card-val{{font-size:24px;font-weight:bold;color:#1e3746}}
  .controls{{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-bottom:14px}}
  input{{padding:8px 10px;border:1px solid #cbd5e1;border-radius:4px;font-size:13px;background:#fff;width:280px}}
  .table-wrap{{overflow-x:auto;background:#fff;border-radius:6px;box-shadow:0 1px 3px rgba(0,0,0,0.08)}}
  table{{width:100%;border-collapse:collapse;font-size:12.5px;min-width:1000px}}
  th{{background:#1e3746;color:#fff;padding:11px 10px;text-align:left;font-weight:600}}
  td{{padding:10px;border-bottom:1px solid #e2e8f0;vertical-align:middle}}
  tr.rent{{background:#eef9e6}}
  tr:hover{{filter:brightness(0.97)}}
  .building{{font-weight:bold;color:#111}}
  .price{{font-weight:bold;font-size:13.5px;color:#111}}
  .room-col{{max-width:420px;line-height:1.45}}
</style></head>
<body>
  <h1>{today_str} 화성시 만세구 새솔동 상가 월세 확인 매물표</h1>
  <div class="badges"><span class="badge-rent">상가 월세 전용 · 연두색</span></div>
  <div class="sub-note">
    수집 당시 네이버 부동산 광고 금액입니다. 월세는 보증금/월세 순서이며 단위는 만원입니다(억 표기는 원문 그대로). 확인일은 최초 등록일과 다를 수 있습니다.
  </div>
  <div class="summary-grid">
    <div class="card"><div class="card-title">전체 상가 월세</div><div class="card-val">{total_cnt}</div></div>
    <div class="card ok"><div class="card-title">호실 확정</div><div class="card-val">{confirmed_cnt}</div></div>
    <div class="card warn"><div class="card-title">상세 표기·미확정</div><div class="card-val">{unconfirmed_cnt}</div></div>
  </div>
  <div class="controls">
    <input type="text" id="q" placeholder="건물명·층·금액·부동산 검색" onkeyup="filter()">
    <b id="cnt"></b>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>건물</th><th>층</th><th>상세 설명·호실 정보</th><th>거래</th><th>광고 금액</th><th>전용 ㎡</th><th>확인일</th><th>광고 등록 부동산</th><th>순번</th></tr></thead>
      <tbody id="tb"></tbody>
    </table>
  </div>
  <script>
    const data={json_str};
    function render(l){{
      document.getElementById('tb').innerHTML=l.map(r=>`<tr class="rent">
        <td class="building">${{r.building}}</td>
        <td>${{r.floor}}</td>
        <td class="room-col">${{r.room_info}}</td>
        <td>${{r.trade_type}}</td>
        <td class="price">${{r.price}}</td>
        <td>${{r.area}}</td>
        <td>${{r.confirm_date}}</td>
        <td>${{r.realtor}}</td>
        <td>${{r.article_no}}</td>
      </tr>`).join('');
      document.getElementById('cnt').innerText=`${{l.length}}건 / 전체 ${{data.length}}건`;
    }}
    function filter(){{
      const q=document.getElementById('q').value.toLowerCase();
      render(data.filter(i=>`${{i.building}} ${{i.floor}} ${{i.room_info}} ${{i.price}} ${{i.realtor}}`.toLowerCase().includes(q)));
    }}
    render(data);
  </script>
</body></html>"""

  os.makedirs(SAVE_DIR, exist_ok=True)
  file_path = os.path.join(SAVE_DIR, f"{today_str}_새솔동_상가월세표.html")
  with open(file_path, "w", encoding="utf-8") as f:
    f.write(html)
  print(f"\n✅ 새솔동 상가 월세 저장 완료! 총 {total_cnt}건: {file_path}")
  os.startfile(file_path)


if __name__ == "__main__":
  run()
