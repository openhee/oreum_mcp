# 오름 지도 URL
https://oreummcp.streamlit.app/
# 오름 추천 MCP 서버 (0단계 골격)

제주 오름 추천을 위한 MCP 서버. tool 1개(`recommend_oreum`)를 노출하며, 사용자
조건(지명/동반자/목적/시각/난이도)으로 `오름.db`를 필터·랭킹하고 후보별로 KASI
(천문) · KMA(단기예보) 데이터를 붙여 JSON으로 반환한다.

이 단계는 end-to-end 관통이 목적이다. 아래는 **이번 단계에서 하지 않는 것**:

- 혼잡도(C축) 관련 로직 없음.
- 왕복 소요시간의 절대 분(分) 예측 없음 — `round_trip_hint`는 DB에 미리 계산된
  참고 범위 문자열을 그대로 노출할 뿐이다.
- 안전 특보 연동 — `gates.safety`는 항상 `{"status":"unknown", "reason":"특보 연동 전(스텁)"}`.
- 왕복시간 컷오프 게이트(일몰 전 하산 가능 여부 판정) — 코드에 `TODO(next)` 주석만
  있고 실제 판정은 없음.
- 사용자 현재 위치 기반 실거리 계산 — `distance_note`는 이유를 설명하는 고정
  문자열 스텁이다 (tool 입력에 사용자 좌표가 없음, `location`은 지명 문자열 필터일 뿐).
- 가중치 기반 정렬 — 지금은 `(난이도 오름차순, 하늘상태 좋은 순)` 단순 정렬뿐.

## 성능: 실시간 조회 상한 (`ENRICH_SAFETY_CAP`)

`location`/`max_difficulty` 필터가 느슨해 매칭 후보가 많이 남으면(예: 인자 없이
호출 — 126개 전체가 후보), 후보마다 KASI+KMA를 순차 호출하는 건 너무 느리다.
그래서 API 호출 전에 DB에 이미 있는 난이도 값만으로 먼저 상위
`max(limit*5, 15)`개로 좁힌 뒤, 그 안에서만 실시간 조회·랭킹한다. 잘려나간 경우
`notes[]`에 몇 개 중 몇 개만 조회했는지 남긴다. 정확도가 중요하면 `location`이나
`max_difficulty`로 후보를 먼저 좁혀서 호출할 것.

## 설치

```
pip install -r requirements.txt
```

(직접 설치: `pip install mcp python-dotenv requests`)

## `.env` 설정

`.env.example`을 `.env`로 복사한 뒤 키를 채운다.

```
KMA_AUTHKEY=       # 기상청 apihub(단기예보 VilageFcst) authKey
KASI_SERVICE_KEY=  # data.go.kr 한국천문연구원 출몰시각 API '일반 인증키(Decoding)'
```

`.env`는 `server.py`와 같은 폴더(`오름.db`가 있는 위치)에 둔다. `server.py`는
자기 파일 위치를 기준으로 `.env`를 찾으므로, 어떤 작업 디렉터리에서 실행하든
(Claude Desktop이 어떤 cwd로 프로세스를 띄우든) 문제 없다.

## 실행 (단독 확인용)

```
python server.py
```

stdio transport로 대기하며, 터미널에서 직접 실행하면 아무 출력 없이 멈춰 있는 것이
정상이다(MCP 클라이언트가 stdin/stdout으로 붙어야 응답한다). 종료는 Ctrl+C.

## Claude Desktop 등록

`claude_desktop_config.json`의 `mcpServers`에 추가:

```json
{
  "mcpServers": {
    "oreum-recommend": {
      "command": "python",
      "args": ["c:\\Users\\user\\Desktop\\oreum_mcp\\server.py"],
      "env": {
        "KMA_AUTHKEY": "여기에_실제_키",
        "KASI_SERVICE_KEY": "여기에_실제_키"
      }
    }
  }
}
```

(`server.py`가 `.env`를 직접 읽으므로 `env` 블록은 생략해도 되지만, Claude
Desktop이 별도 환경으로 프로세스를 띄우는 경우를 대비해 명시하는 것을 권장.)

## `recommend_oreum` 파라미터

| 이름 | 타입 | 기본값 | 설명 |
|---|---|---|---|
| `location` | `str \| None` | `None` | 지명(예: `"성산"`). 지번주소 부분매칭(`LIKE %location%`). |
| `companion` | `str \| None` | `None` | `"child"` \| `"elderly"` \| `"solo"`. 난이도 상한 결정(child/elderly→2, solo/None→무제한). |
| `purpose` | `str \| None` | `None` | `"sunset"` \| `"sunrise"` \| `"view"`. 목표시각·verdict 언어 결정. |
| `datetime_str` | `str \| None` | `None` | ISO 8601. 없으면 서버 현재 시각. |
| `max_difficulty` | `int \| None` | `None` | 난이도 상한(1~5). 지정 시 `companion` 매핑보다 우선. |
| `limit` | `int` | `3` | 반환할 최대 결과 수. |

`location`은 SQL `LIKE`에 그대로 들어가므로 `%`/`_`를 와일드카드로 해석한다(이번
단계에서는 이스케이프하지 않음 — 알려진 제한).

## 반환 스키마

```
{
  "query_echo": { ...입력값과 해석된 값... },
  "count": <int>,           # results[]의 길이 (limit 적용 후)
  "results": [
    {
      "name", "lat", "lng", "kakao_url",
      "difficulty", "difficulty_label", "round_trip_hint",
      "distance_note",       # 스텁 문자열 (사용자 좌표 미입력 사유 설명)
      "astro": { sunrise, sunset, moonrise, moonset, ... } | null,
      "weather_at_target": { target_time, sky, pty, tmp_c, pop_pct, wsd_ms, wind_dir, ... } | null,
      "verdict": "<str>" | null,
      "gates": { "safety": { "status": "unknown", "reason": "특보 연동 전(스텁)" } },
      "confidence": {
        "astro": "ok" | "failed" | "no_coords",
        "weather": "ok" | "failed" | "no_grid",
        "grid_source": "db" | "computed" | null,
        "cache_hit": { "astro": bool, "weather": bool }
      }
    },
    ...
  ],
  "notes": [ "<필터 사유 / 부분 실패 사유 / 요약>", ... ]
}
```

## 예시 호출/응답

호출:

```json
{"location": "성산", "purpose": "sunset", "companion": "elderly", "limit": 2}
```

응답(축약, 예시):

```json
{
  "query_echo": {
    "location": "성산", "companion": "elderly", "purpose": "sunset",
    "datetime_str": null, "resolved_datetime": "2026-07-26T18:02:11",
    "max_difficulty": null, "resolved_max_difficulty": 2, "limit": 2
  },
  "count": 1,
  "results": [
    {
      "name": "말미오름", "lat": 33.4621355, "lng": 126.7803343,
      "kakao_url": "http://place.map.kakao.com/8194279",
      "difficulty": 1, "difficulty_label": "아주 쉬움", "round_trip_hint": "약 15~30분",
      "distance_note": "사용자 위치 좌표가 입력값에 없어 실제 거리 계산 불가 (...)",
      "astro": {"sunrise": "05:48", "sunset": "19:41", "moonrise": "-", "moonset": "14:02", ...},
      "weather_at_target": {
        "target_time": "2026-07-26 20:00", "sky_code": "1", "sky": "맑음",
        "pty_code": "0", "pty": "없음", "tmp_c": 24.0, "pop_pct": 10,
        "wsd_ms": 3.2, "vec_deg": 270.0, "wind_dir": "서"
      },
      "verdict": "노을·조망 양호",
      "gates": {"safety": {"status": "unknown", "reason": "특보 연동 전(스텁)"}},
      "confidence": {"astro": "ok", "weather": "ok", "grid_source": "db",
                     "cache_hit": {"astro": false, "weather": false}}
    }
  ],
  "notes": ["총 3개 후보 중 상위 1개 반환."]
}
```

(`companion="elderly"`로 난이도≤2 필터가 걸려 "성산" 지역 후보 3개 중 조건에 맞는
1개만 반환된 상황을 가정한 예시.)

## 다음 단계 TODO (이번 스코프 아님)

- 가중치 기반 랭킹(바람/강수확률/사용자 선호 가중합 등)으로 단순 정렬 교체.
- 왕복시간 컷오프 게이트: `(sunset - now)` 가용시간과 `round_trip_hint` 등급상단값
  배수를 비교해 pass/warn/block 판정.
- 사용자 좌표 입력 파라미터 추가 후 `distance_note`를 실제 거리 계산으로 교체.
- 안전 특보(기상특보 등) 연동 후 `gates.safety` 스텁 교체.
