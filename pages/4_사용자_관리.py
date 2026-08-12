"""
pages/4_👥_사용자_관리.py
관리자가 다른 사용자의 역할(일반/관리자)을 바꾸거나, PIN을 초기화하거나, 계정을 삭제할 수 있는 화면.
관리자만 접근 가능합니다.
"""

import secrets

import streamlit as st

import db
import auth

st.set_page_config(page_title="사용자 관리", page_icon="👥", layout="wide")

db.init_db()
current = auth.require_admin()
auth.sidebar_user_info()

st.title("👥 사용자 관리")
st.caption("관리자만 볼 수 있는 화면입니다. 역할 변경, PIN 초기화, 계정 삭제는 모두 활동 이력에 기록됩니다.")

users = db.list_users()
admin_count = sum(1 for u in users if u["role"] == db.ROLE_ADMIN)

if admin_count <= 1:
    st.warning(
        f"⚠️ 현재 관리자가 {admin_count}명뿐입니다. 그 관리자가 PIN을 잊거나 자리를 비우면 "
        "아무도 이 화면을 못 쓰게 될 수 있어요. **관리자를 최소 2명으로 유지하는 것을 권장합니다.**"
    )


def _generate_temp_pin():
    return f"{secrets.randbelow(10000):04d}"


st.caption(f"총 {len(users)}명 (관리자 {admin_count}명)")

for u in users:
    with st.container(border=True):
        role_label = "👑 관리자" if u["role"] == db.ROLE_ADMIN else "일반 사용자"
        dept = f" ({u['department']})" if u["department"] else ""
        st.markdown(
            f"**{u['username']}**{dept} · {role_label}  \n"
            f"가입일: {u['created_at']}"
        )

        b1, b2, b3 = st.columns(3)

        # ---- 역할 변경 ----
        with b1:
            if u["role"] == db.ROLE_ADMIN:
                if st.button("일반 사용자로 강등", key=f"demote_{u['id']}", use_container_width=True):
                    ok, msg = db.set_user_role(u["id"], db.ROLE_USER, actor=current["username"])
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()
            else:
                if st.button("관리자로 승격", key=f"promote_{u['id']}", use_container_width=True):
                    ok, msg = db.set_user_role(u["id"], db.ROLE_ADMIN, actor=current["username"])
                    (st.success if ok else st.error)(msg)
                    if ok:
                        st.rerun()

        # ---- PIN 초기화 ----
        with b2:
            reset_key = f"reset_open_{u['id']}"
            if st.button("PIN 초기화", key=f"reset_btn_{u['id']}", use_container_width=True):
                st.session_state[reset_key] = True

            if st.session_state.get(reset_key):
                temp_pin = st.session_state.get(f"temp_pin_{u['id']}")
                if temp_pin is None:
                    temp_pin = _generate_temp_pin()
                    st.session_state[f"temp_pin_{u['id']}"] = temp_pin

                st.info(f"임시 PIN: **{temp_pin}**  \n이 PIN을 본인에게 전달해주세요. 확정 전까지는 실제로 바뀌지 않습니다.")
                rc1, rc2 = st.columns(2)
                if rc1.button("✅ 이 PIN으로 초기화 확정", key=f"reset_confirm_{u['id']}"):
                    ok, msg = db.reset_password(u["id"], temp_pin, actor=current["username"])
                    (st.success if ok else st.error)(msg)
                    st.session_state[reset_key] = False
                    st.session_state[f"temp_pin_{u['id']}"] = None
                if rc2.button("취소", key=f"reset_cancel_{u['id']}"):
                    st.session_state[reset_key] = False
                    st.session_state[f"temp_pin_{u['id']}"] = None

        # ---- 계정 삭제 ----
        with b3:
            del_confirm_key = f"del_confirm_{u['id']}"
            if st.button("🗑️ 계정 삭제", key=f"del_btn_{u['id']}", use_container_width=True):
                st.session_state[del_confirm_key] = True

            if st.session_state.get(del_confirm_key):
                st.warning(
                    f"**'{u['username']}'** 계정을 정말 삭제하시겠습니까? "
                    "이 사람이 등록했던 보고/일정 기록은 그대로 남지만, 계정 자체는 되돌릴 수 없습니다."
                )
                dc1, dc2 = st.columns(2)
                if dc1.button("✅ 삭제 확정", key=f"del_confirm_yes_{u['id']}", use_container_width=True):
                    ok, msg = db.delete_user(u["id"], actor=current["username"])
                    (st.success if ok else st.error)(msg)
                    st.session_state[del_confirm_key] = False
                    if ok:
                        st.rerun()
                if dc2.button("취소", key=f"del_confirm_no_{u['id']}", use_container_width=True):
                    st.session_state[del_confirm_key] = False
                    st.rerun()
