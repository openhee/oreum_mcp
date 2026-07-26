import folium
import pandas as pd
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

DATA_PATH = "오름_데이터셋.csv"

st.set_page_config(page_title="제주 오름 지도", layout="wide")


def fmt_m(value):
    if pd.isna(value) or value == "":
        return "정보없음"
    return f"{value} m"


@st.cache_data
def load_data():
    df = pd.read_csv(DATA_PATH, encoding="cp949", sep="\t")
    df["위도"] = pd.to_numeric(df["위도"], errors="coerce")
    df["경도"] = pd.to_numeric(df["경도"], errors="coerce")
    df["표고"] = pd.to_numeric(df["표고"], errors="coerce")
    df["비고"] = pd.to_numeric(df["비고"], errors="coerce")
    return df.dropna(subset=["위도", "경도"])


df = load_data()

st.title("제주 오름 지도")
st.caption("오름_데이터셋.csv를 기반으로 위치를 표시합니다.")

st.sidebar.header("오름 검색")
options = ["전체 보기"] + sorted(df["오름명"].unique().tolist())
selected = st.sidebar.selectbox("오름명을 선택하세요", options)

if selected != "전체 보기":
    row = df.loc[df["오름명"] == selected].iloc[0]
    center = [row["위도"], row["경도"]]
    zoom = 14
    st.sidebar.subheader(selected)
    st.sidebar.write(f"**구분**: {row['오름여부']}")
    st.sidebar.write(f"**지번주소**: {row['지번주소']}")
    st.sidebar.write(f"**표고**: {fmt_m(row['표고'])}")
    st.sidebar.write(f"**비고**: {fmt_m(row['비고'])}")
    if pd.notna(row.get("관리시설")) and row.get("관리시설"):
        st.sidebar.write(f"**관리시설**: {row['관리시설']}")
    if pd.notna(row.get("카카오맵_url")) and row.get("카카오맵_url"):
        st.sidebar.markdown(f"[카카오맵에서 보기]({row['카카오맵_url']})")
else:
    center = [df["위도"].mean(), df["경도"].mean()]
    zoom = 10

m = folium.Map(location=center, zoom_start=zoom)
cluster = MarkerCluster().add_to(m)

for _, row in df.iterrows():
    is_selected = row["오름명"] == selected
    kakao_link = (
        f"<p><a href=\"{row['카카오맵_url']}\" target=\"_blank\">카카오맵에서 보기</a></p>"
        if pd.notna(row.get("카카오맵_url")) and row.get("카카오맵_url")
        else ""
    )
    popup_html = f"""
    <div style="width:220px">
        <h4>{row['오름명']}</h4>
        <p><b>구분</b>: {row['오름여부']}</p>
        <p><b>지번주소</b>: {row['지번주소']}</p>
        <p><b>표고</b>: {fmt_m(row['표고'])}</p>
        <p><b>비고</b>: {fmt_m(row['비고'])}</p>
        {kakao_link}
    </div>
    """
    folium.Marker(
        location=[row["위도"], row["경도"]],
        tooltip=row["오름명"],
        popup=folium.Popup(popup_html, max_width=300),
        icon=folium.Icon(color="red" if is_selected else "blue"),
    ).add_to(cluster)

st_folium(m, width=1200, height=700, returned_objects=[])
