"""
날짜를 골라 그날의 박스오피스 순위를 보여주는 스트림릿 앱
- KOBIS(영화진흥위원회) 일별 박스오피스 API 사용
- 스트림릿 클라우드 배포용 (secrets.toml에 KOBIS_KEY 필요)
- 고를 수 있는 가장 늦은 날짜는 '어제(한국 시간 기준)'까지
"""

import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

# ----------------------------------------------------------------
# 0) 기본 설정
# ----------------------------------------------------------------
st.set_page_config(page_title="박스오피스", page_icon="🎬", layout="wide")

# KOBIS API 주소 (공식 문서에 나온 그대로)
API_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"

# 숫자로 바꿔야 하는 컬럼들 (API에서는 전부 문자열로 옴)
NUMERIC_COLS = ["rank", "rankInten", "audiCnt", "audiAcc", "scrnCnt", "showCnt"]


# ----------------------------------------------------------------
# 1) '어제' 날짜를 한국 시간(KST) 기준으로 계산
#    - 배포 서버의 시계가 한국 시간이 아닐 수 있으므로,
#      UTC 시각을 구한 뒤 +9시간을 더해서 직접 한국 시간을 만든다.
#    - 달력에서 고를 수 있는 가장 늦은 날짜로도 쓰인다
#      (오늘 것은 아직 집계 전이라 어제까지만 고를 수 있게 함).
# ----------------------------------------------------------------
def get_yesterday_kst_date():
    """한국 시간(KST) 기준 '어제' 날짜를 date 객체로 반환한다."""
    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(timezone.utc).astimezone(kst)
    return (now_kst - timedelta(days=1)).date()


# ----------------------------------------------------------------
# 2) KOBIS API 호출 함수
#    - st.cache_data로 같은 날짜(target_dt)에 대해서는
#      한 시간(3600초) 동안 결과를 기억해두고 재사용한다.
#      (같은 날짜를 다시 물어도 API를 또 부르지 않음)
# ----------------------------------------------------------------
@st.cache_data(ttl=3600)
def fetch_box_office(target_dt: str):
    """
    KOBIS 일별 박스오피스 API를 호출한다.
    성공 시: (True, 영화 목록 리스트)
    실패 시: (False, 사용자에게 보여줄 안내 메시지)
    """
    key = st.secrets.get("KOBIS_KEY")
    if not key:
        return False, "인증키(KOBIS_KEY)가 설정되어 있지 않습니다. 스트림릿 클라우드의 Secrets 설정을 확인해 주세요."

    params = {"key": key, "targetDt": target_dt}

    # 2-1) 네트워크 요청 자체가 실패하는 경우 (타임아웃, 서버 다운 등)
    try:
        response = requests.get(API_URL, params=params, timeout=10)
    except requests.exceptions.RequestException:
        return False, "KOBIS 서버에 연결하지 못했습니다. 인터넷 연결 상태나 KOBIS 서버 상태를 확인해 주세요."

    # 2-2) HTTP 상태코드가 200이 아닌 경우
    if response.status_code != 200:
        return False, f"KOBIS 서버가 오류를 반환했습니다 (상태코드: {response.status_code}). 잠시 후 다시 시도해 주세요."

    # 2-3) 응답이 JSON 형식이 아닌 경우
    try:
        data = response.json()
    except ValueError:
        return False, "KOBIS 서버 응답을 해석할 수 없습니다(JSON 형식이 아님). 요청 주소나 파라미터를 확인해 주세요."

    # 2-4) 인증키가 틀린 경우 등 -> 상태코드는 200이지만 faultInfo 상자가 옴
    if "faultInfo" in data:
        message = data["faultInfo"].get("message", "알 수 없는 오류")
        return False, f"KOBIS API가 오류를 반환했습니다: {message}\n인증키(KOBIS_KEY)가 올바른지 확인해 주세요."

    # 2-5) 예상한 구조(boxOfficeResult)가 없는 경우
    box_office_result = data.get("boxOfficeResult")
    if not box_office_result:
        return False, "응답에서 boxOfficeResult를 찾을 수 없습니다. API 응답 구조가 변경되었을 수 있습니다."

    movie_list = box_office_result.get("dailyBoxOfficeList")

    # 2-6) 영화 목록이 비어 있는 경우 (예: 아직 집계되지 않은 날짜)
    if not movie_list:
        return False, "그날은 아직 집계 전입니다. 다른 날짜를 골라 주세요."

    return True, movie_list


# ----------------------------------------------------------------
# 3) 문자열로 온 숫자 컬럼들을 실제 숫자(int)로 변환
#    - 정렬이나 그래프에 쓰려면 문자열이 아니라 숫자여야 하기 때문
# ----------------------------------------------------------------
def to_numeric_dataframe(movie_list: list) -> pd.DataFrame:
    df = pd.DataFrame(movie_list)
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ----------------------------------------------------------------
# 3-1) 표에 보여줄 영화명 꾸미기
#    - 전날보다 순위가 오르면(rankInten 양수) 빨간 위 화살표(🔺)
#    - 전날보다 순위가 내리면(rankInten 음수) 파란 아래 화살표(🔽)
#    - 누적관객이 100만 명을 넘으면 트로피(🏆) 이모지
# ----------------------------------------------------------------
def build_display_name(row) -> str:
    if row["rankInten"] > 0:
        arrow = "🔺"
    elif row["rankInten"] < 0:
        arrow = "🔽"
    else:
        arrow = ""

    trophy = "🏆" if row["audiAcc"] >= 1_000_000 else ""

    parts = [p for p in [arrow, row["movieNm"], trophy] if p]
    return " ".join(parts)


# ----------------------------------------------------------------
# 4) 메인 화면 구성
# ----------------------------------------------------------------
def main():
    st.title("🎬 박스오피스")

    # 달력에서 날짜를 고르되, 가장 늦은 날짜는 '어제(한국 시간 기준)'까지만 허용
    # (오늘 것은 아직 집계 전이라 고를 수 없게 함)
    max_date = get_yesterday_kst_date()
    selected_date = st.date_input(
        "조회할 날짜를 골라 주세요",
        value=max_date,
        max_value=max_date,
    )
    target_dt = selected_date.strftime("%Y%m%d")
    pretty_date = f"{target_dt[:4]}년 {target_dt[4:6]}월 {target_dt[6:]}일"
    st.caption(f"조회 날짜: {pretty_date}")

    # API 호출 (실패 시 안내 메시지를 보여주고 화면을 멈춤)
    ok, result = fetch_box_office(target_dt)
    if not ok:
        st.error(result)
        st.stop()

    df = to_numeric_dataframe(result)
    df = df.sort_values("rank")  # 순위대로 정렬

    # -------------------------------
    # 4-1) 1위 영화: 지표 카드 3장
    # -------------------------------
    top_movie = df.iloc[0]
    st.subheader(f"{pretty_date} 1위: {top_movie['movieNm']}")

    col1, col2, col3 = st.columns(3)
    col1.metric("관객수", f"{int(top_movie['audiCnt']):,}명")
    col2.metric("누적 관객수", f"{int(top_movie['audiAcc']):,}명")
    col3.metric("스크린수", f"{int(top_movie['scrnCnt']):,}개")

    st.divider()

    # -------------------------------
    # 4-2) 관객수 상위 5편 막대그래프
    # -------------------------------
    st.subheader("관객수 상위 5편")
    top5 = df.sort_values("audiCnt", ascending=False).head(5)
    chart_df = top5.set_index("movieNm")[["audiCnt"]]
    chart_df.columns = ["관객수"]
    st.bar_chart(chart_df)

    st.divider()

    # -------------------------------
    # 4-3) 전체 순위 표
    # -------------------------------
    st.subheader("전체 순위")
    st.caption("🔺 전날보다 순위 상승 · 🔽 전날보다 순위 하락 · 🏆 누적관객 100만 명 이상")

    display_df = df[["rank", "movieNm", "openDt", "audiCnt", "audiAcc", "scrnCnt"]].copy()
    display_df["movieNm"] = df.apply(build_display_name, axis=1)
    display_df.columns = ["순위", "영화명", "개봉일", "관객수", "누적관객", "스크린수"]

    # 표에서 보기 좋게 천 단위 콤마 서식 적용
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "관객수": st.column_config.NumberColumn(format="%d"),
            "누적관객": st.column_config.NumberColumn(format="%d"),
            "스크린수": st.column_config.NumberColumn(format="%d"),
        },
    )


if __name__ == "__main__":
    main()
