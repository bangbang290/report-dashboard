"""
app.py
메인 진입 화면 = 실시간 현황 (요약 뷰).
"우리 팀 순서가 언제인지" 확인용으로 다들 상시로 띄워놓는 화면입니다.
왼쪽 사이드바에서 "보고 진행현황"(전체 관리) / "월간 일정" / "활동 이력" / "사용자 관리" 페이지로 이동할 수 있습니다.
"""

import time
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import db
import auth
from utils import format_schedule_display

st.set_page_config(page_title="국장님 보고 - 실시간 현황", page_icon="📢", layout="wide")

db.init_db()
auth.require_login()
auth.sidebar_user_info()

st.title("📢 국장님 보고 실시간 현황")
st.caption("시작 전 · 진행 중 상태인 보고만 예정 시각 순서로 보여줍니다. 전체 이력이나 등록/수정은 왼쪽 '보고 진행현황' 페이지를 이용해주세요.")

with st.sidebar:
    st.divider()
    auto_refresh = st.toggle("자동 새로고침 (30초)", value=False)
    if st.button("🔄 지금 새로고침", use_container_width=True):
        st.rerun()

if auto_refresh:
    # 화면이 멈춘 것처럼 보이지 않게, sleep 대신 가벼운 타이머 컴포넌트로 자동 새로고침
    st_autorefresh(interval=30_000, key="live_status_autorefresh")

db.auto_update_statuses()  # 예정 시각 지난 건 진행중/완료로 자동 전환

active_reports = [
    r for r in db.list_reports(order_by="scheduled_date")
    if r["status"] in ("시작 전", "진행 중")
]

if not active_reports:
    st.info("현재 대기 중이거나 진행 중인 보고가 없습니다.")
else:
    col_status_order = {"진행 중": 0, "시작 전": 1}
    active_reports.sort(key=lambda r: (col_status_order.get(r["status"], 2), r["scheduled_date"] or "9999"))

    for r in active_reports:
        badge = "🟢 진행 중" if r["status"] == "진행 중" else "⚪ 시작 전"
        cols = st.columns([3, 2, 2])
        with cols[0]:
            st.markdown(f"### {r['team_name'] or '(이름 미입력)'}")
        with cols[1]:
            st.markdown(f"**{badge}**")
        with cols[2]:
            st.markdown(format_schedule_display(r["scheduled_date"], r["scheduled_time"]))
        st.divider()

st.caption(f"마지막 갱신: {time.strftime('%Y-%m-%d %H:%M:%S')}")
