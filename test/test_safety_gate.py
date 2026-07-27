# -*- coding: utf-8 -*-
"""
특보 안전 게이트(gates.safety) 검증 — 목데이터, 네트워크 호출 없음.

wrn_now_data_new 응답(CSV형 텍스트)을 문자열로 목킹해 parse_safety_text() →
filter_safety_relevant() → match_safety_gate() 파이프라인을 직접 재현한다:
  1) 제주시동부에 강풍주의보 → 그 구역 오름 warning, detail 정확
  2) 폭염만 발효 → 입산관련 아님(필터 동작) → clear
  3) 파싱 0건(빈 응답) → 전 오름 clear
  4) reg_up 매칭 케이스(응답이 상위코드 L1091300으로 온 경우) → 해당 하위 오름 warning
"""
from server import filter_safety_relevant, match_safety_gate, parse_safety_text


def run_case(title, raw_text, warn_reg_id, warn_reg_up):
    print(f"\n=== {title} ===")
    parsed = parse_safety_text(raw_text)
    relevant = filter_safety_relevant(parsed)
    gate = match_safety_gate(warn_reg_id, warn_reg_up, relevant)
    print(f"parsed 건수     : {len(parsed)}")
    print(f"입산관련 필터 후: {len(relevant)}")
    print(f"status          : {gate['status']}")
    print(f"detail          : {gate['detail']}")
    print(f"warnings        : {gate['warnings']}")
    return gate


# 1) 제주시동부에 강풍주의보 → warning, detail 정확
text_1 = """#START7777
# REG_UP  REG_UP_KO  REG_ID  REG_KO  TM_FC  TM_EF  WRN  LVL  CMD  ED_TM
L1091300, 제주시(산지 제외), L1091330, 제주시동부, 202607261000, 202607261100, 강풍  , 주의보    , 변경,  ,=
#7777END"""
gate1 = run_case(
    "1) 제주시동부 강풍주의보 → warning",
    text_1, warn_reg_id="L1091330", warn_reg_up="L1091300",
)
assert gate1["status"] == "warning", gate1
assert gate1["detail"] == "제주시동부 강풍주의보 발효", gate1
assert gate1["warnings"] == [{"type": "강풍", "level": "주의보", "reg": "제주시동부"}], gate1

# 2) 폭염만 발효 → 입산관련 아님 → clear (필터 동작 확인)
text_2 = """#START7777
# REG_UP  REG_UP_KO  REG_ID  REG_KO  TM_FC  TM_EF  WRN  LVL  CMD  ED_TM
L1091300, 제주시(산지 제외), L1091330, 제주시동부, 202607261000, 202607261100, 폭염  , 주의    , 변경,  ,=
#7777END"""
gate2 = run_case(
    "2) 폭염만 발효 → 입산 무관 필터링 → clear",
    text_2, warn_reg_id="L1091330", warn_reg_up="L1091300",
)
assert gate2["status"] == "clear", gate2
assert gate2["warnings"] == [], gate2

# 3) 파싱 0건(빈 응답) → 전 오름 clear
gate3 = run_case(
    "3) 빈 응답 → clear",
    "", warn_reg_id="L1091330", warn_reg_up="L1091300",
)
assert gate3["status"] == "clear", gate3
assert gate3["detail"] == "발효 중인 입산 관련 특보 없음", gate3

# 4) reg_up 매칭 케이스 — 특보가 상위코드(L1091300) 단위로 발효, 오름은 하위코드(L1091330)
text_4 = """#START7777
# REG_UP  REG_UP_KO  REG_ID  REG_KO  TM_FC  TM_EF  WRN  LVL  CMD  ED_TM
L1091300, 제주시(산지 제외), L1091300, 제주시(산지 제외), 202607261000, 202607261100, 호우  , 경보    , 변경,  ,=
#7777END"""
gate4 = run_case(
    "4) 상위코드(L1091300) 단위 발효 → 하위 오름(warn_reg_id=L1091330) warning",
    text_4, warn_reg_id="L1091330", warn_reg_up="L1091300",
)
assert gate4["status"] == "warning", gate4
assert gate4["detail"] == "제주시(산지 제외) 호우경보 발효", gate4

# 4-b) 대조군: reg_up이 다른 오름은 매칭되지 않아야 함(clear)
gate4b = run_case(
    "4-b) 대조군: 다른 상위코드(L1090000) 오름은 매칭 안 됨 → clear",
    text_4, warn_reg_id="L1090510", warn_reg_up="L1090000",
)
assert gate4b["status"] == "clear", gate4b

print("\n모든 시나리오 통과.")
