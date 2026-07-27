# -*- coding: utf-8 -*-
"""
제주 오름 지도 대시보드 (Streamlit).

오름.db(server.py와 동일 DB)를 지도에 뿌리고, 마커 클릭 시 server.py의
KASI(천문)/KMA(단기예보/특보현황) 조회 로직을 그대로 재사용해 실시간 날씨·
일출몰·입산 관련 특보 정보를 붙인다. API 호출 로직은 재구현하지 않고
server.py를 모듈로 import해 그대로 쓴다.
"""
import sqlite3
from datetime import datetime, timedelta

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

import server as api

st.set_page_config(page_title="제주 오름 지도", layout="wide")

DIFFICULTY_COLOR = {1: "green", 2: "blue", 3: "orange", 4: "red", 5: "darkred"}
SEARCH_COLOR = "purple"
COORD_EPS = 1e-4

# st.markdown의 ":color[]" 구문은 folium 아이콘 색상과 팔레트가 달라(darkred/purple 미지원),
# 범례 표시용으로 가장 가까운 streamlit 지원 색상에 매핑한다.
LEGEND_COLOR = {"green": "green", "blue": "blue", "orange": "orange", "red": "red",
                "darkred": "red", "purple": "violet"}

# 팝업(HTML)에서 gates.safety.status 별로 표시할 문구/색상 (server.py의 status 정의 그대로:
# clear/warning/unknown — "blocked"는 없음, 특보구역 단위 근사이므로 warning이어도 안내만 함).
SAFETY_HTML = {
    "clear": lambda detail: f"<b>특보</b>: <span style='color:#1a7f37'>{detail}</span>",
    "warning": lambda detail: f"<b>특보</b>: <span style='color:#b00020'>{detail}</span>",
    "unknown": lambda detail: f"<b>특보</b>: <span style='color:#666'>{detail}</span>",
}


@st.cache_data
def load_data():
    with sqlite3.connect(api.DB_PATH) as con:
        df = pd.read_sql_query(f'SELECT rowid, * FROM "{api.TABLE}"', con)
    return df.dropna(subset=[api.COL_LAT, api.COL_LON]).reset_index(drop=True)


@st.cache_data(ttl=600)
def load_active_warnings():
    """특보현황을 앱 전체에서 1회(10분 캐시)만 조회한다. 실패해도 위험이 아니라
    "정보 없음"으로 취급한다 (server.py와 동일 원칙)."""
    try:
        warnings, status_code = api.fetch_active_safety_warnings()
        return warnings, status_code, None
    except api.SafetyLookupError as e:
        return None, None, str(e)


def fmt(value, suffix=""):
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return "정보없음"
    return f"{value}{suffix}"


def fetch_live_info(row, active_warnings):
    """server.py의 KASI/KMA/특보 호출을 그대로 재사용해 오름 하나의 실시간 정보를 가져온다."""
    lat, lon = row[api.COL_LAT], row[api.COL_LON]
    now = datetime.now()
    date_str = now.strftime("%Y%m%d")
    live = {"astro": None, "weather": None, "safety": None, "error": None}

    warn_reg_id = row.get("warn_reg_id") if not pd.isna(row.get("warn_reg_id")) else None
    warn_reg_up = row.get("warn_reg_up") if not pd.isna(row.get("warn_reg_up")) else None
    live["safety"] = api.match_safety_gate(warn_reg_id, warn_reg_up, active_warnings)

    try:
        live["astro"] = api.kasi_times(lat, lon, date_str)
    except api.AstroLookupError as e:
        live["error"] = f"천문 조회 실패: {e}"

    nx, ny = row[api.COL_NX], row[api.COL_NY]
    if pd.isna(nx) or pd.isna(ny):
        nx, ny = api.latlon_to_grid(lat, lon)
    base_date, base_time = api.latest_base(now)
    try:
        slots = api.kma_slots(int(nx), int(ny), base_date, base_time)
        snapped = api.snap_to_hour(now)
        target_key = (snapped.strftime("%Y%m%d"), snapped.strftime("%H00"))
        raw = slots.get(target_key)
        if raw is None:
            # 방금 발표된 슬롯(latest_base의 10분 여유)은 KMA 쪽 게시 지연으로 아직
            # 데이터가 안 실렸을 수 있다 — 이미 확정 게시됐을 직전 발표(3시간 전)로 1회 재시도.
            prev_base_date, prev_base_time = api.latest_base(now - timedelta(hours=3))
            if (prev_base_date, prev_base_time) != (base_date, base_time):
                prev_slots = api.kma_slots(int(nx), int(ny), prev_base_date, prev_base_time)
                raw = prev_slots.get(target_key)
        if raw is not None:
            live["weather"] = api.normalize_weather(raw, snapped)
        else:
            live["error"] = (live["error"] + " / " if live["error"] else "") + "목표 정시 예보 슬롯 없음"
    except api.WeatherLookupError as e:
        live["error"] = (live["error"] + " / " if live["error"] else "") + f"기상 조회 실패: {e}"

    return live


def build_popup_html(row, live):
    parts = [f"<div style='width:240px'><h4>{row[api.COL_NAME]}</h4>"]
    parts.append(f"<p><b>구분</b>: {fmt(row['오름여부'])}</p>")
    parts.append(f"<p><b>지번주소</b>: {fmt(row[api.COL_ADDR])}</p>")
    parts.append(f"<p><b>난이도</b>: {fmt(row['difficulty_label'])}</p>")
    parts.append(f"<p><b>왕복 소요</b>: {fmt(row['round_trip_hint'])}</p>")
    parts.append(f"<p><b>표고</b>: {fmt(row['표고'], ' m')} · <b>비고</b>: {fmt(row['비고'], ' m')}</p>")
    if row.get("카카오맵_url"):
        parts.append(f"<p><a href=\"{row['카카오맵_url']}\" target=\"_blank\">카카오맵에서 보기</a></p>")

    if live:
        if live.get("astro"):
            a = live["astro"]
            parts.append(f"<p><b>일출</b>: {a['sunrise']} · <b>일몰</b>: {a['sunset']}</p>")
        if live.get("weather"):
            w = live["weather"]
            parts.append(
                f"<p><b>{w['target_time']} 날씨</b>: {fmt(w['sky'])} · {fmt(w['tmp_c'], '℃')} · "
                f"강수확률 {fmt(w['pop_pct'], '%')} · {fmt(w['wind_dir'])} {fmt(w['wsd_ms'], 'm/s')}</p>"
            )
        if live.get("safety"):
            parts.append(f"<p>{SAFETY_HTML[live['safety']['status']](live['safety']['detail'])}</p>")
        if live.get("error"):
            parts.append(f"<p style='color:#b00020'><b>실시간 정보 오류</b>: {live['error']}</p>")

    parts.append("</div>")
    return "".join(parts)


def find_row_by_latlng(df, lat, lng):
    dist = (df[api.COL_LAT] - lat).abs() + (df[api.COL_LON] - lng).abs()
    idx = dist.idxmin()
    if dist.loc[idx] > COORD_EPS:
        return None
    return df.loc[idx]


df = load_data()
active_warnings, safety_status_code, safety_error = load_active_warnings()

if "weather_cache" not in st.session_state:
    st.session_state.weather_cache = {}
if "selected_rowid" not in st.session_state:
    st.session_state.selected_rowid = None
if "last_click_key" not in st.session_state:
    st.session_state.last_click_key = None

st.title("제주 오름 지도")
st.caption("오름.db 기반 지도 · 마커 클릭 시 실시간 날씨/일출몰 정보를 붙여 보여준다.")

st.sidebar.header("오름 검색")
query = st.sidebar.text_input("오름명 일부를 입력하세요", "")
matched = df[df[api.COL_NAME].str.contains(query, na=False)] if query else df.iloc[0:0]

if query:
    st.sidebar.write(f"검색 결과: {len(matched)}건")
    if len(matched) == 0:
        st.sidebar.caption("일치하는 오름이 없습니다.")

st.sidebar.divider()
st.sidebar.caption("입산 관련 특보 현황 (KMA)")
if safety_error:
    st.sidebar.warning(f"특보 조회 실패 - 정보 없음 ({safety_error})")
elif not active_warnings:
    st.sidebar.success("현재 발효 중인 입산 관련 특보 없음")
else:
    st.sidebar.error(f"현재 입산 관련 특보 {len(active_warnings)}건 발효 중")
    for w in active_warnings:
        st.sidebar.caption(f"- {w['reg_ko']} {w['wrn']}{w['lvl']}")

st.sidebar.divider()
st.sidebar.caption("마커 색상 (난이도)")
for level, color in DIFFICULTY_COLOR.items():
    label = "난이도 5 (진한 빨강)" if color == "darkred" else f"난이도 {level}"
    st.sidebar.markdown(f"- :{LEGEND_COLOR[color]}[●] {label}")
st.sidebar.markdown(f"- :{LEGEND_COLOR[SEARCH_COLOR]}[★] 검색 결과")

matched_rowids = set(matched["rowid"])

if not matched.empty:
    center = [matched.iloc[0][api.COL_LAT], matched.iloc[0][api.COL_LON]]
    zoom = 13
elif st.session_state.selected_rowid is not None:
    sel = df.loc[df["rowid"] == st.session_state.selected_rowid]
    if not sel.empty:
        center = [sel.iloc[0][api.COL_LAT], sel.iloc[0][api.COL_LON]]
        zoom = 14
    else:
        center = [df[api.COL_LAT].mean(), df[api.COL_LON].mean()]
        zoom = 10
else:
    center = [df[api.COL_LAT].mean(), df[api.COL_LON].mean()]
    zoom = 10

m = folium.Map(location=center, zoom_start=zoom)

for _, row in df.iterrows():
    rowid = row["rowid"]
    live = st.session_state.weather_cache.get(rowid)
    is_hit = rowid in matched_rowids
    if is_hit:
        icon = folium.Icon(color=SEARCH_COLOR, icon="star", prefix="fa")
    else:
        color = DIFFICULTY_COLOR.get(row["difficulty"], "gray")
        icon = folium.Icon(color=color, icon="mountain", prefix="fa")
    folium.Marker(
        location=[row[api.COL_LAT], row[api.COL_LON]],
        tooltip=row[api.COL_NAME],
        popup=folium.Popup(build_popup_html(row, live), max_width=300),
        icon=icon,
    ).add_to(m)

ret = st_folium(m, width=None, use_container_width=True, height=650,
                 returned_objects=["last_object_clicked"])

clicked = ret.get("last_object_clicked") if ret else None
if clicked:
    click_key = (round(clicked["lat"], 6), round(clicked["lng"], 6))
    if click_key != st.session_state.last_click_key:
        st.session_state.last_click_key = click_key
        hit = find_row_by_latlng(df, clicked["lat"], clicked["lng"])
        if hit is not None:
            st.session_state.selected_rowid = hit["rowid"]
            with st.spinner(f"{hit[api.COL_NAME]} 실시간 정보 조회 중..."):
                st.session_state.weather_cache[hit["rowid"]] = fetch_live_info(hit, active_warnings)
            st.rerun()

if st.session_state.selected_rowid is not None:
    sel_rows = df.loc[df["rowid"] == st.session_state.selected_rowid]
    if not sel_rows.empty:
        sel = sel_rows.iloc[0]
        live = st.session_state.weather_cache.get(sel["rowid"], {})
        with st.container(border=True):
            st.subheader(f"🌄 {sel[api.COL_NAME]} 실시간 정보")
            c1, c2, c3 = st.columns(3)
            c1.metric("난이도", fmt(sel["difficulty_label"]))
            c2.metric("왕복 소요", fmt(sel["round_trip_hint"]))
            c3.metric("표고 / 비고", f"{fmt(sel['표고'])} / {fmt(sel['비고'])} m")

            astro = live.get("astro")
            if astro:
                a1, a2, a3 = st.columns(3)
                a1.metric("일출", astro["sunrise"])
                a2.metric("일몰", astro["sunset"])
                a3.metric("월출 / 월몰", f"{astro['moonrise']} / {astro['moonset']}")

            weather = live.get("weather")
            if weather:
                st.caption(f"기준 시각: {weather['target_time']}")
                w1, w2, w3, w4 = st.columns(4)
                w1.metric("하늘", fmt(weather["sky"]))
                w2.metric("기온", fmt(weather["tmp_c"], "℃"))
                w3.metric("강수확률", fmt(weather["pop_pct"], "%"))
                w4.metric("바람", f"{fmt(weather['wind_dir'])} {fmt(weather['wsd_ms'], 'm/s')}")

            safety = live.get("safety")
            if safety:
                if safety["status"] == "warning":
                    st.error(f"⚠️ {safety['detail']}")
                elif safety["status"] == "clear":
                    st.success(f"✅ {safety['detail']}")
                else:
                    st.info(f"ℹ️ {safety['detail']}")

            if live.get("error"):
                st.warning(live["error"])
                if st.button("실시간 정보 다시 조회", key=f"retry_{sel['rowid']}"):
                    with st.spinner(f"{sel[api.COL_NAME]} 실시간 정보 재조회 중..."):
                        st.session_state.weather_cache[sel["rowid"]] = fetch_live_info(sel, active_warnings)
                    st.rerun()
            if sel.get("카카오맵_url"):
                st.markdown(f"[카카오맵에서 보기]({sel['카카오맵_url']})")
