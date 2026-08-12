"""
pages/2_월간_일정.py
국장님 월간 일정을 노션처럼 달력(캘린더) 형태로 보여주는 화면.
일정 시간은 텍스트로 직접 타이핑하지 않고 드롭다운에서 고릅니다 (시간 없는 일정도 등록 가능).
캘린더에서 일정을 클릭하면 그 일정의 수정/삭제 폼이 아래에 나타납니다.
캘린더 상호작용이 안 맞으면, 맨 아래 "전체 목록" 에서도 똑같이 수정/삭제할 수 있습니다.
"""

import datetime as dt
import streamlit as st

try:
    from streamlit_calendar import calendar as st_calendar
    CALENDAR_AVAILABLE = True
except ImportError:
    CALENDAR_AVAILABLE = False

import db
import auth
from utils import format_date_kr, parse_event_name, compose_event_name, SCHEDULE_TIME_OPTIONS

st.set_page_config(page_title="국장님 월간 일정", page_icon="📅", layout="wide")

db.init_db()
user = auth.require_login()
auth.sidebar_user_info()

st.title("📅 국장님 월간 일정")

NO_TIME_LABEL = "(시간 없음)"
TIME_CHOICES = [NO_TIME_LABEL] + SCHEDULE_TIME_OPTIONS


def _time_index(current_time: str) -> int:
    if current_time and current_time in TIME_CHOICES:
        return TIME_CHOICES.index(current_time)
    return 0


def _time_choices_with_current(current_time: str) -> list:
    """기존 시간이 10분 단위 목록에 없는 값이면(옛날 데이터 등) 임시로 끼워넣음."""
    if current_time and current_time not in TIME_CHOICES:
        return [NO_TIME_LABEL] + sorted(set(SCHEDULE_TIME_OPTIONS) | {current_time})
    return TIME_CHOICES


# ---------------- 새 일정 등록 ----------------
with st.expander("➕ 새 일정 등록", expanded=False):
    with st.form("add_schedule_form", clear_on_submit=True):
        c1, c2, c3 = st.columns([2, 1, 2])
        with c1:
            event_title = st.text_input("일정명 (예: 확대간부회의)")
        with c2:
            event_time = st.selectbox("시간", TIME_CHOICES)
        with c3:
            event_date = st.date_input("날짜")
        submitted = st.form_submit_button("등록", use_container_width=True)

    if submitted:
        if not event_title.strip():
            st.error("일정명을 입력해주세요.")
        else:
            time_value = None if event_time == NO_TIME_LABEL else event_time
            final_name = compose_event_name(time_value, event_title)
            db.register_schedule(final_name, event_date.isoformat(), user["username"])
            st.success("등록되었습니다.")
            st.rerun()

st.divider()

# ---------------- 달력 뷰 ----------------
if not CALENDAR_AVAILABLE:
    st.warning(
        "📦 달력 보기를 쓰려면 `streamlit-calendar` 패키지 설치가 필요합니다.\n\n"
        "터미널에서 아래 명령어를 실행한 뒤, 앱을 다시 실행(`python -m streamlit run app.py`)해주세요.\n\n"
        "```\npython -m pip install streamlit-calendar\n```\n\n"
        "설치 전까지는 바로 아래 '🗒️ 전체 목록으로 보기/수정'에서 그대로 등록/조회/수정/삭제하실 수 있습니다."
    )
else:
    all_items = db.list_schedule()
    events = [
        {"id": str(it["id"]), "title": it["event_name"], "start": it["event_date"], "allDay": True}
        for it in all_items
    ]

    calendar_options = {
        "initialView": "dayGridMonth",
        "locale": "ko",
        "height": 680,
        "headerToolbar": {"left": "prev,next today", "center": "title", "right": "dayGridMonth,listMonth"},
    }

    st.caption("일정을 클릭하면 아래에 수정/삭제 화면이 나타납니다.")
    calendar_state = st_calendar(events=events, options=calendar_options, key="director_month_calendar")

    clicked_item = None
    if calendar_state and calendar_state.get("eventClick"):
        try:
            clicked_id = int(calendar_state["eventClick"]["event"]["id"])
            clicked_item = db.get_schedule_item(clicked_id)
        except (KeyError, TypeError, ValueError):
            clicked_item = None

    if clicked_item:
        can_edit = auth.is_admin() or clicked_item["created_by"] == user["username"]
        st.subheader(f"📌 선택한 일정: {clicked_item['event_name']}")
        st.caption(f"{format_date_kr(clicked_item['event_date'])} · 등록자: {clicked_item['created_by']}")

        if not can_edit:
            st.info("본인이 등록한 일정이 아니라 수정/삭제할 수 없습니다. (관리자만 가능)")
        else:
            current_time, current_title = parse_event_name(clicked_item["event_name"])
            time_choices = _time_choices_with_current(current_time)

            with st.form("calendar_edit_form"):
                e1, e2 = st.columns([1, 2])
                with e1:
                    new_time = st.selectbox("시간", time_choices, index=_time_index(current_time) if current_time in time_choices else 0)
                with e2:
                    new_title = st.text_input("일정명", value=current_title)
                try:
                    default_date = dt.date.fromisoformat(clicked_item["event_date"])
                except ValueError:
                    default_date = dt.date.today()
                new_date = st.date_input("날짜", value=default_date)
                save = st.form_submit_button("저장", use_container_width=True)

            if save:
                if not new_title.strip():
                    st.error("일정명을 입력해주세요.")
                else:
                    time_value = None if new_time == NO_TIME_LABEL else new_time
                    final_name = compose_event_name(time_value, new_title)
                    db.edit_schedule(clicked_item["id"], final_name, new_date.isoformat(), actor=user["username"])
                    st.success("수정되었습니다.")
                    st.rerun()

            if st.button("🗑️ 이 일정 삭제", key="calendar_delete_btn"):
                st.session_state["calendar_confirm_delete"] = True

            if st.session_state.get("calendar_confirm_delete"):
                st.warning("정말 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.")
                cc1, cc2 = st.columns(2)
                if cc1.button("✅ 삭제 확정", key="calendar_confirm_yes", use_container_width=True):
                    db.remove_schedule(clicked_item["id"], actor=user["username"])
                    st.session_state["calendar_confirm_delete"] = False
                    st.success("삭제되었습니다.")
                    st.rerun()
                if cc2.button("취소", key="calendar_confirm_no", use_container_width=True):
                    st.session_state["calendar_confirm_delete"] = False
                    st.rerun()

st.divider()

# ---------------- 전체 목록 (달력 클릭이 안 맞을 때를 위한 대체 수단) ----------------
with st.expander("🗒️ 전체 목록으로 보기 / 수정", expanded=False):
    today = dt.date.today()
    month_choice = st.text_input("조회할 월 (YYYY-MM, 비워두면 전체)", value=today.strftime("%Y-%m"))

    items = db.list_schedule(month=month_choice.strip() if month_choice.strip() else None)
    st.caption(f"총 {len(items)}건")

    for it in items:
        can_edit = auth.is_admin() or it["created_by"] == user["username"]
        with st.container(border=True):
            st.markdown(
                f"**{it['event_name']}**  \n"
                f"{format_date_kr(it['event_date'])} · 등록자: {it['created_by']}"
            )

            if can_edit:
                edit_key = f"sched_edit_{it['id']}"
                confirm_key = f"sched_confirm_delete_{it['id']}"

                btn_col1, btn_col2 = st.columns(2)
                if btn_col1.button("수정", key=f"sched_edit_btn_{it['id']}", use_container_width=True):
                    st.session_state[edit_key] = not st.session_state.get(edit_key, False)
                    st.session_state[confirm_key] = False

                if btn_col2.button("🗑️ 삭제", key=f"sched_delete_btn_{it['id']}", use_container_width=True):
                    st.session_state[confirm_key] = True
                    st.session_state[edit_key] = False

                if st.session_state.get(confirm_key):
                    st.warning(f"**'{it['event_name']}'** 일정을 정말 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.")
                    cc1, cc2 = st.columns(2)
                    if cc1.button("✅ 삭제 확정", key=f"sched_confirm_yes_{it['id']}", use_container_width=True):
                        db.remove_schedule(it["id"], user["username"])
                        st.session_state[confirm_key] = False
                        st.success("삭제되었습니다.")
                        st.rerun()
                    if cc2.button("취소", key=f"sched_confirm_no_{it['id']}", use_container_width=True):
                        st.session_state[confirm_key] = False
                        st.rerun()

                if st.session_state.get(edit_key):
                    current_time, current_title = parse_event_name(it["event_name"])
                    time_choices = _time_choices_with_current(current_time)
                    with st.form(f"sched_edit_form_{it['id']}"):
                        se1, se2 = st.columns([1, 2])
                        with se1:
                            new_time = st.selectbox(
                                "시간", time_choices,
                                index=time_choices.index(current_time) if current_time in time_choices else 0,
                                key=f"sched_time_{it['id']}",
                            )
                        with se2:
                            new_title = st.text_input("일정명", value=current_title, key=f"sched_name_{it['id']}")
                        try:
                            default_date = dt.date.fromisoformat(it["event_date"])
                        except ValueError:
                            default_date = today
                        new_date = st.date_input("날짜", value=default_date, key=f"sched_date_{it['id']}")
                        save = st.form_submit_button("저장", use_container_width=True)

                    if save:
                        if not new_title.strip():
                            st.error("일정명을 입력해주세요.")
                        else:
                            time_value = None if new_time == NO_TIME_LABEL else new_time
                            final_name = compose_event_name(time_value, new_title)
                            db.edit_schedule(it["id"], final_name, new_date.isoformat(), actor=user["username"])
                            st.session_state[edit_key] = False
                            st.success("수정되었습니다.")
                            st.rerun()
