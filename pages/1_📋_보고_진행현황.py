"""
pages/1_보고_진행현황.py
- 새 보고 등록 (과 드롭다운 + 팀/보고자 수기입력, 시각은 09:30~18:00 드롭다운에서 선택)
- 오늘 일정: 진행 전 / 진행 중 / 완료 3분할
- 예약 현황: 오늘 이후로 등록된 예약 목록
- 전체 이력: 검색/필터 가능한 전체 목록 (과거 이력 포함)
상태는 사람이 직접 고르지 않고, 예정 시각 기준으로 자동으로 진행 전 → 진행 중 → 완료로 넘어갑니다.
"""

import datetime as dt
import streamlit as st

import db
import auth
from utils import format_schedule_display, REPORT_TIME_OPTIONS

MIN_GAP_MINUTES = 20

st.set_page_config(page_title="보고 진행현황", page_icon="📋", layout="wide")

db.init_db()
user = auth.require_login()
auth.sidebar_user_info()
db.auto_update_statuses()

st.title("📋 보고 진행현황")


def _render_conflict_error(conflicts, suggested, verb="등록"):
    names = ", ".join(f"{c['team_name'] or '(이름 미입력)'}({c['scheduled_time']})" for c in conflicts)
    st.error(
        f"⏱️ 이미 예약된 시간({names})과 {MIN_GAP_MINUTES}분 미만으로 겹칩니다. "
        f"**{suggested}** 이후로 {verb}해주세요. (또는 아래 체크박스로 무시하고 {verb} 가능)"
    )


def _booked_times_caption(date_str, exclude_id=None):
    """선택한 날짜에 이미 잡힌 시간들을 미리 보여줌 (등록/수정 전에 참고용)."""
    existing = db.get_times_for_date(date_str, exclude_id=exclude_id)
    if existing:
        booked = ", ".join(sorted(e["scheduled_time"] for e in existing))
        st.caption(f"📌 {date_str} 에 이미 예약된 시간: {booked}")
    else:
        st.caption(f"📌 {date_str} 에는 아직 예약된 시간이 없습니다.")


# ---------------- 새 보고 등록 ----------------
with st.expander("➕ 새 보고 등록", expanded=False):
    st.caption(
        f"⏱️ 보고 예정 시각은 09:30~18:00 사이에서 목록으로 고르면 되고, "
        f"같은 날짜에 이미 예약된 시간과 {MIN_GAP_MINUTES}분 미만으로 겹치면 자동으로 막고 다음 가능한 시간을 알려드립니다. "
        f"등록하면 상태는 자동으로 '시작 전'으로 시작해서, 예정 시각이 되면 '진행 중'으로, 20분 지나면 '완료'로 자동 전환됩니다."
    )

    add_date = st.date_input("보고 예정일", value=dt.date.today(), key="add_report_date")
    _booked_times_caption(add_date.isoformat())

    with st.form("add_report_form", clear_on_submit=False):
        c1, c2 = st.columns(2)
        with c1:
            department = st.selectbox("과", db.DEPARTMENT_OPTIONS)
            department_custom = ""
            if department == "기타(직접입력)":
                department_custom = st.text_input("과 (직접입력)")
            team_detail = st.text_input("팀 / 보고자")
        with c2:
            scheduled_time = st.selectbox("보고 예정 시각", REPORT_TIME_OPTIONS)
        memo = st.text_area("비고 (선택)", height=80)
        override_gap = st.checkbox(f"⚠️ {MIN_GAP_MINUTES}분 간격 무시하고 그래도 이 시간으로 등록")
        submitted = st.form_submit_button("등록", use_container_width=True)

    if submitted:
        final_department = department_custom.strip() if department == "기타(직접입력)" else department

        if not team_detail.strip():
            st.error("팀 / 보고자를 입력해주세요.")
        elif department == "기타(직접입력)" and not final_department:
            st.error("과를 직접 입력해주세요.")
        else:
            ok, conflicts, suggested = db.register_report(
                department=final_department,
                team_detail=team_detail.strip(),
                scheduled_date=add_date.isoformat(),
                scheduled_time=scheduled_time,
                memo=memo.strip(),
                created_by=user["username"],
                min_gap=MIN_GAP_MINUTES,
                override=override_gap,
            )
            if ok:
                st.success("등록되었습니다.")
                st.rerun()
            else:
                _render_conflict_error(conflicts, suggested, verb="등록")


def render_report_card(r, user, key_prefix=""):
    """보고 항목 하나를 카드로 그리고, 권한 있으면 수정/삭제까지 처리."""
    can_edit = auth.is_admin() or r["created_by"] == user["username"]
    kp = f"{key_prefix}{r['id']}"
    with st.container(border=True):
        top = st.columns([3, 2, 2, 1, 1])
        top[0].markdown(f"**{r['team_name'] or '(이름 미입력)'}**")
        top[1].markdown(r["status"])
        top[2].markdown(format_schedule_display(r["scheduled_date"], r["scheduled_time"]))

        if can_edit:
            edit_key = f"edit_open_{kp}"
            confirm_key = f"confirm_delete_{kp}"

            if top[3].button("수정", key=f"edit_btn_{kp}"):
                st.session_state[edit_key] = not st.session_state.get(edit_key, False)
                st.session_state[confirm_key] = False

            if top[4].button("🗑️ 삭제", key=f"delete_btn_{kp}"):
                st.session_state[confirm_key] = True
                st.session_state[edit_key] = False

            if st.session_state.get(confirm_key):
                st.warning(f"**'{r['team_name'] or '(이름 미입력)'}'** 항목을 정말 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.")
                cc1, cc2 = st.columns(2)
                if cc1.button("✅ 삭제 확정", key=f"confirm_yes_{kp}", use_container_width=True):
                    db.remove_report(r["id"], user["username"])
                    st.session_state[confirm_key] = False
                    st.success("삭제되었습니다.")
                    st.rerun()
                if cc2.button("취소", key=f"confirm_no_{kp}", use_container_width=True):
                    st.session_state[confirm_key] = False
                    st.rerun()

            if st.session_state.get(edit_key):
                try:
                    default_date = dt.date.fromisoformat(r["scheduled_date"]) if r["scheduled_date"] else dt.date.today()
                except ValueError:
                    default_date = dt.date.today()
                new_date = st.date_input("보고 예정일", value=default_date, key=f"date_{kp}")
                _booked_times_caption(new_date.isoformat(), exclude_id=r["id"])

                # 기존 시각이 09:30~18:00 10분 간격 목록에 없는 값(예: 옛날 노션 데이터)이어도
                # 목록에 임시로 끼워넣어서 고를 수 있게 함
                time_options = list(REPORT_TIME_OPTIONS)
                current_time = r["scheduled_time"] or ""
                if current_time and current_time not in time_options:
                    time_options = sorted(set(time_options) | {current_time})
                time_index = time_options.index(current_time) if current_time in time_options else 0

                with st.form(f"edit_form_{kp}"):
                    e1, e2 = st.columns(2)
                    with e1:
                        current_dept = r["department"] or ""
                        if current_dept in db.DEPARTMENT_OPTIONS:
                            dept_index = db.DEPARTMENT_OPTIONS.index(current_dept)
                        else:
                            dept_index = len(db.DEPARTMENT_OPTIONS) - 1  # 기타(직접입력)
                        new_department = st.selectbox(
                            "과", db.DEPARTMENT_OPTIONS, index=dept_index, key=f"dept_{kp}"
                        )
                        new_department_custom = ""
                        if new_department == "기타(직접입력)":
                            default_custom = current_dept if current_dept not in db.DEPARTMENT_OPTIONS else ""
                            new_department_custom = st.text_input("과 (직접입력)", value=default_custom, key=f"dept_custom_{kp}")
                        new_team_detail = st.text_input(
                            "팀 / 보고자", value=r["team_detail"] or r["team_name"], key=f"team_{kp}"
                        )
                    with e2:
                        new_time = st.selectbox("보고 예정 시각", time_options, index=time_index, key=f"time_{kp}")
                    new_memo = st.text_area("비고", value=r["memo"] or "", key=f"memo_{kp}")
                    edit_override_gap = st.checkbox(
                        f"⚠️ {MIN_GAP_MINUTES}분 간격 무시하고 그래도 이 시간으로 저장", key=f"override_{kp}"
                    )
                    save = st.form_submit_button("저장", use_container_width=True)

                if save:
                    final_new_department = new_department_custom.strip() if new_department == "기타(직접입력)" else new_department
                    if not new_team_detail.strip():
                        st.error("팀 / 보고자를 입력해주세요.")
                    else:
                        ok, conflicts, suggested = db.edit_report(
                            r["id"], final_new_department, new_team_detail.strip(),
                            new_date.isoformat(), new_time, new_memo.strip(),
                            actor=user["username"], min_gap=MIN_GAP_MINUTES, override=edit_override_gap,
                        )
                        if ok:
                            st.session_state[edit_key] = False
                            st.success("수정되었습니다.")
                            st.rerun()
                        else:
                            _render_conflict_error(conflicts, suggested, verb="저장")

        if r["memo"]:
            st.caption(f"📝 {r['memo']}")


st.divider()

tab_today, tab_upcoming, tab_all = st.tabs(["📍 오늘 일정", "🗓️ 예약 현황 (오늘 이후)", "🗃️ 전체 이력"])

# ---------------- 오늘 일정 (3분할) ----------------
with tab_today:
    today_str = dt.date.today().isoformat()
    today_reports = [r for r in db.list_reports() if r["scheduled_date"] == today_str]

    col_before, col_during, col_done = st.columns(3)
    sections = [
        ("⚪ 진행 전", "시작 전", col_before),
        ("🟢 진행 중", "진행 중", col_during),
        ("✅ 완료", "완료", col_done),
    ]
    for label, status_name, col in sections:
        with col:
            st.subheader(label)
            subset = sorted(
                [r for r in today_reports if r["status"] == status_name],
                key=lambda r: r["scheduled_time"] or "99:99",
            )
            st.caption(f"{len(subset)}건")
            if not subset:
                st.caption("— 없음 —")
            for r in subset:
                render_report_card(r, user, key_prefix="today_")

# ---------------- 예약 현황 (오늘 이후) ----------------
with tab_upcoming:
    today_str = dt.date.today().isoformat()
    upcoming = [r for r in db.list_reports() if (r["scheduled_date"] or "9999") > today_str]
    upcoming.sort(key=lambda r: (r["scheduled_date"] or "9999", r["scheduled_time"] or "99:99"))
    st.caption(f"총 {len(upcoming)}건")
    if not upcoming:
        st.info("등록된 예약이 없습니다.")
    for r in upcoming:
        render_report_card(r, user, key_prefix="upcoming_")

# ---------------- 전체 이력 ----------------
with tab_all:
    filter_cols = st.columns([2, 3, 2])
    with filter_cols[0]:
        status_filter = st.selectbox("상태 필터", ["전체"] + db.STATUS_OPTIONS, key="all_status_filter")
    with filter_cols[1]:
        search_text = st.text_input("이름 검색 (부분 일치)", key="all_search_text")

    reports = db.list_reports(status_filter=status_filter)
    if search_text.strip():
        reports = [r for r in reports if search_text.strip() in (r["team_name"] or "")]

    st.caption(f"총 {len(reports)}건")

    for r in reports:
        render_report_card(r, user, key_prefix="all_")
