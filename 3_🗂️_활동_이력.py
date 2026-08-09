"""
pages/3_🗂️_활동_이력.py
누가 언제 무엇을 등록/수정/삭제했는지 보여주는 감사 로그 화면. 관리자만 볼 수 있습니다.
"""

import streamlit as st

import db
import auth

st.set_page_config(page_title="활동 이력", page_icon="🗂️", layout="wide")

db.init_db()
auth.require_admin()
auth.sidebar_user_info()

st.title("🗂️ 활동 이력")
st.caption("최근 등록/수정/삭제 기록입니다. 관리자에게만 보입니다.")

ACTION_LABELS = {
    "create": "🟢 등록",
    "update": "✏️ 수정",
    "delete": "🗑️ 삭제",
    "role_change": "👤 권한 변경",
    "password_reset": "🔑 비밀번호 초기화",
}
TABLE_LABELS = {
    "reports": "보고 진행현황",
    "monthly_schedule": "월간 일정",
    "users": "사용자 계정",
}

col1, col2 = st.columns(2)
with col1:
    table_filter = st.selectbox("대상", ["전체"] + list(TABLE_LABELS.values()))
with col2:
    limit = st.number_input("최근 몇 건 볼까요", min_value=20, max_value=2000, value=200, step=20)

logs = db.list_activity(limit=int(limit))

if table_filter != "전체":
    reverse_map = {v: k for k, v in TABLE_LABELS.items()}
    target_table = reverse_map[table_filter]
    logs = [l for l in logs if l["table_name"] == target_table]

st.caption(f"{len(logs)}건")

for log in logs:
    action_label = ACTION_LABELS.get(log["action"], log["action"])
    table_label = TABLE_LABELS.get(log["table_name"], log["table_name"])
    with st.container(border=True):
        c1, c2, c3 = st.columns([2, 2, 2])
        c1.markdown(f"**{action_label}** · {table_label}")
        c2.markdown(f"실행자: **{log['actor']}**")
        c3.markdown(log["created_at"])
        if log["detail"]:
            st.caption(log["detail"])
