# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

This repo builds a cleaned, deduplicated dataset of Jeju Island's *oreum* (volcanic cones) for use in a future "오름 추천" (oreum recommendation) MCP. Today it is a small data pipeline plus a Streamlit map viewer — there is no MCP server implementation yet, no dependency manifest (`requirements.txt`/`pyproject.toml`), and no test suite. Code comments and identifiers are in Korean; preserve that when editing.

## Commands

No package manifest exists. Required packages, install as needed:

```
pip install folium pandas streamlit streamlit-folium
```

Build the merged dataset (`오름_데이터셋.csv`) from the seed list + gov't CSV + API dump:

```
python build_orum_dataset.py
```

Refresh `api_orum.json` from the live 제주데이터허브 API (requires a project key, network access):

```
python fetch.py
```
(`fetch.py` just calls `fetch_api(project_key)` from `build_orum_dataset.py` — edit the key inline or import and call it yourself.)

Run the map viewer:

```
streamlit run map.py
```

## Data pipeline architecture (`build_orum_dataset.py`)

The pipeline joins three independent, non-overlapping data sources into one output CSV, because no single source has both elevation and coordinates:

1. **Seed list** — `133개의_오름명소.txt`: scraped "장소/지점" blocks separated by `더보기`. Only `장소` (place) blocks are kept as oreum entries; `지점` (point) blocks are dropped. This defines the canonical list of oreum names + addresses.
2. **Gov't CSV** — `제주특별자치도_오름현황_*.csv` (glob'd, latest by sort order, **CP949** encoding): sole source of 표고 (absolute elevation) and 비고 (relative height / prominence). **Use 비고, not 표고, for difficulty calculations** — this is called out explicitly in the module docstring.
3. **API dump** — `api_orum.json` (제주데이터허브 오름위치 API, optional — pipeline degrades gracefully if the file is absent): sole source of lat/long and the Kakao Map URL.

These three are joined independently onto the seed list (`join_csv`, then `join_api`), not against each other.

### Join/matching strategy

Oreum names are **not unique** across sources (e.g. 민오름/당오름/붉은오름/세미오름 all repeat with different locations), so matching uses a fallback chain (see `match()`):

1. Manual `ALIAS` dict keyed by `(seed_name, 읍면동)` — for confirmed name variants between sources (e.g. 셋개오리→샛개오리, 금악오름→금오름). Add new entries here only once you've confirmed the mapping via address, not by name alone.
2. Exact normalized-name match (`norm`: strips whitespace and a leading "한라산" prefix).
3. Core-word match (`core`: additionally strips parenthetical aliases and a trailing 오름/봉/악/산 suffix).
4. If multiple candidates remain, disambiguate by 번지 (lot number, `bunji()`) then by 읍면동 (`emd()`) parsed out of the address.

If nothing resolves, the row is left blank and reported at the end of the run under "표고 미확보" / "좌표 미확보", along with any same-core API name candidates (`_api_cand`) to help you extend `ALIAS`.

### Output

`오름_데이터셋.csv` (UTF-8 with BOM) columns: 오름명, 오름여부, 지번주소, 위도, 경도, 비고, 표고, 카카오맵_url, 관리시설, 표고비고출처, 좌표출처. The `*출처` (source) columns record which source and match method (별칭/단일/주소) filled each value, for auditing.

`용머리해안` and `한라산` are hardcoded in `NON_OREUM` as non-oreum landmarks that skip the elevation/prominence join (오름여부 = "명소" instead of "오름").

## Encoding notes

Source CSVs use **CP949**, not UTF-8 — `제주특별자치도_오름현황_*.csv` is comma-delimited, `제주특별자치도_제주오름요약설명_20211130.csv` is CP949 **and** tab-delimited (see `map.py`'s `pd.read_csv(..., encoding="cp949", sep="\t")`). The generated `오름_데이터셋.csv` and `api_orum.json` are UTF-8. Get this wrong and Korean text becomes garbled rather than raising a clean error — verify encoding/delimiter when touching any CSV I/O.
