# -*- coding: utf-8 -*-
"""
왕복시간 게이트(gates.round_trip) 검증 — 목데이터, API/DB 호출 없음.

server.py 의 resolve_target_and_gate() 를 합성 astro 딕셔너리로 직접 호출해
4가지 정책 분기를 재현한다:
  1) 낮 + 일몰 목표 + 쉬운 오름           → pass
  2) 일몰 49분 전 + 힘든 오름(등급4,애기) → fail (margin 음수)
  3) 20:00 현재 + 일몰 목표               → 내일 일몰로 롤오버, target_basis=sunset_tomorrow
  4) 22:00 현재 + purpose=None            → night

시나리오 3만 "내일 KASI 재조회"가 필요한데, 실제 네트워크를 부르지 않도록
kasi_fetch 자리에 고정값을 돌려주는 mock 함수를 주입한다(resolve_target_and_gate 는
이를 위해 kasi_fetch 를 의존성 주입 파라미터로 받는다).
"""
from datetime import datetime

from server import resolve_target_and_gate


def run_case(title, **kwargs):
    print(f"\n=== {title} ===")
    notes = []
    kwargs.setdefault("kasi_cache", {})
    kwargs.setdefault("notes", notes)
    target_moment, gate = resolve_target_and_gate(**kwargs)
    print(f"target_time    : {gate['target_time']}")
    print(f"target_basis   : {gate['target_basis']}")
    print(f"status         : {gate['status']}")
    print(f"est_min        : {gate['est_min']}")
    print(f"remaining_min  : {gate['remaining_min']}")
    print(f"margin_min     : {gate['margin_min']}")
    print(f"note           : {gate['note']}")
    if notes:
        print(f"notes[]        : {notes}")
    return gate


# 1) 낮 + 일몰 목표 + 쉬운 오름 → pass
astro_today_1 = {"sunrise": "05:30", "sunset": "19:38"}
gate1 = run_case(
    "1) 낮(16:00) + 일몰 목표 + 난이도1(무동반) → pass 기대",
    name="테스트오름1", purpose="sunset",
    requested_dt=datetime(2026, 7, 27, 16, 0),
    astro_today=astro_today_1, target_date_str="20260727",
    lat=33.4, lon=126.5, difficulty=1, companion=None,
)
assert gate1["status"] == "pass", gate1
assert gate1["target_basis"] == "sunset_today", gate1

# 2) 일몰 49분 전 + 힘든 오름(등급4, 아이 동반) → fail
astro_today_2 = {"sunrise": "05:30", "sunset": "19:38"}
gate2 = run_case(
    "2) 일몰 49분 전(18:49) + 난이도4 + 아이 동반 → fail 기대",
    name="테스트오름2", purpose="sunset",
    requested_dt=datetime(2026, 7, 27, 18, 49),
    astro_today=astro_today_2, target_date_str="20260727",
    lat=33.4, lon=126.5, difficulty=4, companion="child",
)
assert gate2["status"] == "fail", gate2
assert gate2["margin_min"] < 0, gate2

# 3) 20:00 현재(오늘 일몰 지남) + 일몰 목표 → 내일 일몰로 롤오버
astro_today_3 = {"sunrise": "05:30", "sunset": "19:38"}


def mock_kasi_fetch_tomorrow(lat, lon, locdate):
    assert locdate == "20260728"  # 내일 날짜로 재조회하는지 확인
    return {"sunrise": "05:31", "sunset": "19:37"}


gate3 = run_case(
    "3) 20:00 현재 + 일몰 목표(오늘 일몰 지남) → 내일 일몰 기준",
    name="테스트오름3", purpose="sunset",
    requested_dt=datetime(2026, 7, 27, 20, 0),
    astro_today=astro_today_3, target_date_str="20260727",
    lat=33.4, lon=126.5, difficulty=2, companion=None,
    kasi_fetch=mock_kasi_fetch_tomorrow,
)
assert gate3["target_basis"] == "sunset_tomorrow", gate3
assert gate3["target_time"].startswith("2026-07-28"), gate3

# 4) 22:00 현재 + purpose=None(야간) → night
astro_today_4 = {"sunrise": "05:30", "sunset": "19:38"}
gate4 = run_case(
    "4) 22:00 현재 + purpose=None(오늘 일몰~일출 사이) → night 기대",
    name="테스트오름4", purpose=None,
    requested_dt=datetime(2026, 7, 27, 22, 0),
    astro_today=astro_today_4, target_date_str="20260727",
    lat=33.4, lon=126.5, difficulty=3, companion=None,
)
assert gate4["status"] == "night", gate4

print("\n모든 시나리오 통과.")
