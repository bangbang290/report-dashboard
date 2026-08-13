"""
auth.py
로그인 / 회원가입 폼과 세션 상태 관리.
각 페이지 맨 위에서 require_login() 을 호출하면 됩니다.

로그인은 이름 + 4자리 숫자 PIN 방식입니다. (짧아서 외우기 쉽지만, 그만큼 5회 연속
틀리면 5분간 잠기는 보호장치가 더 중요합니다 - db.py 의 로그인 시도 제한 참고)

로그인 유지(새로고침해도/페이지 이동해도/다른 앱 갔다와도 로그인 안 풀리게) 방식:
- 로그인 성공 시, 서버(DB)에 세션 토큰을 만들고 그 토큰을 브라우저 쿠키에 저장합니다.
  쿠키는 URL(페이지 경로)이 바뀌어도 브라우저에 계속 붙어있기 때문에, 페이지를 이동해도
  안 사라집니다. (이전 버전에서 시도했던 "URL 뒤에 토큰 붙이기" 방식은 페이지 이동 시
  스트림릿이 새 페이지 주소를 처음부터 다시 만들면서 토큰이 빠지는 문제가 있어서 폐기했습니다.)
- 쿠키는 브라우저에 "설치"되고 나서 읽어오기까지 아주 잠깐(한 rerun 정도) 시간이 걸립니다.
  이걸 "쿠키가 없다 = 로그인 안 됨"으로 착각하면 오히려 로그인이 자꾸 풀리는 것처럼
  보이는 문제가 생기므로, 쿠키가 "아직 로딩 중"인 상태와 "진짜로 없음"인 상태를 구분해서
  처리합니다 (_try_restore_session_from_cookie 참고).
"""

import time
from datetime import datetime, timedelta

import streamlit as st
import extra_streamlit_components as stx

import db
from utils import validate_pin

COOKIE_NAME = "report_dashboard_session"


def _init_session():
    if "user" not in st.session_state:
        st.session_state["user"] = None


def _get_cookie_manager():
    # 같은 CookieManager 인스턴스를 세션 안에서 재사용 (매 rerun마다 새로 만들면 중복 컴포넌트 문제가 생길 수 있음)
    if "_cookie_manager" not in st.session_state:
        st.session_state["_cookie_manager"] = stx.CookieManager(key="report_dashboard_cookie_manager")
    return st.session_state["_cookie_manager"]


def current_user():
    _init_session()
    return st.session_state["user"]


def is_admin() -> bool:
    user = current_user()
    return bool(user) and user.get("role") == db.ROLE_ADMIN


def logout():
    token = st.session_state.get("_session_token")
    if token:
        db.delete_session(token)
    cookie_manager = _get_cookie_manager()
    try:
        cookie_manager.delete(COOKIE_NAME)
    except KeyError:
        pass  # 쿠키가 이미 없는 경우
    time.sleep(0.5)
    st.session_state["user"] = None
    st.session_state["_session_token"] = None
    st.rerun()


def _do_login(username: str, pin: str):
    result = db.verify_user(username, pin)
    if result and "__locked_until__" in result:
        st.error(
            f"🔒 로그인을 5회 연속 실패해서 잠시 잠겼습니다. {result['__locked_until__']} 이후 다시 시도해주세요."
        )
        return
    if not result:
        st.error("이름 또는 PIN이 올바르지 않습니다.")
        return

    token = db.create_session(result["username"])
    cookie_manager = _get_cookie_manager()
    cookie_manager.set(
        COOKIE_NAME, token,
        expires_at=datetime.now() + timedelta(days=db.SESSION_MAX_AGE_DAYS),
        key="set_session_cookie",
    )
    # 쿠키가 브라우저에 실제로 저장되는 데 아주 잠깐 시간이 걸리는데, 그걸 기다리지 않고
    # 바로 rerun 해버리면 저장이 중간에 끊겨서 다음 새로고침 때 쿠키가 없는 것처럼 보이는
    # 문제가 있었습니다. 그래서 짧게 대기했다가 넘어갑니다.
    time.sleep(0.5)
    st.session_state["user"] = result
    st.session_state["_session_token"] = token
    st.rerun()


def _login_form():
    st.subheader("🔐 로그인")
    with st.form("login_form"):
        username = st.text_input("이름", help="가입할 때 등록한 로그인용 이름입니다.", key="login_username")
        pin = st.text_input("PIN (숫자 4자리)", type="password", max_chars=4, key="login_pin")
        submitted = st.form_submit_button("로그인", use_container_width=True)
    if submitted:
        _do_login(username, pin)


def _signup_form():
    st.subheader("📝 최초 등록")
    st.caption("처음 접속하는 분은 여기서 이름과 PIN(숫자 4자리)을 등록해주세요. 이후에는 로그인 탭을 이용하시면 됩니다.")

    if not db.any_admin_exists():
        st.warning("⚠️ 아직 관리자 계정이 없습니다. 지금 가입하는 분이 반드시 '관리자로 등록'을 체크해주세요.")

    with st.form("signup_form"):
        username = st.text_input("이름 (로그인용 계정명, 예: 홍길동)", key="signup_username")
        department = st.text_input("부서 (선택)", key="signup_department")
        pin = st.text_input("PIN (숫자 4자리)", type="password", max_chars=4, key="signup_pin")
        pin2 = st.text_input("PIN 확인", type="password", max_chars=4, key="signup_pin2")
        make_admin = False
        if not db.any_admin_exists():
            make_admin = st.checkbox("이 계정을 관리자 계정으로 등록 (최초 1회만 표시됩니다)", key="signup_make_admin")
        submitted = st.form_submit_button("등록하기", use_container_width=True)

    if submitted:
        ok_pin, pin_msg = validate_pin(pin)
        if not ok_pin:
            st.error(pin_msg)
            return
        if pin != pin2:
            st.error("PIN이 서로 일치하지 않습니다. 다시 입력해주세요.")
            return
        if not username.strip():
            st.error("이름을 입력해주세요.")
            return

        role = db.ROLE_ADMIN if make_admin else db.ROLE_USER
        ok, msg = db.create_user(username, pin, department, role)
        if ok:
            st.success(msg + " 이제 로그인 탭에서 로그인해주세요.")
        else:
            st.error(msg)


def _try_restore_session_from_cookie():
    """
    쿠키에 로그인 토큰이 남아있으면 자동으로 로그인 상태를 복원.
    반환값: "restored"(복원됨) / "loading"(쿠키 컴포넌트가 아직 로딩 중, 잠깐 기다려야 함)
           / "none"(진짜로 로그인 정보 없음)
    """
    cookie_manager = _get_cookie_manager()
    cookies = cookie_manager.get_all()
    if cookies is None:
        # 브라우저에 쿠키 컴포넌트가 아직 로드되는 중. 이걸 "로그인 안 됨"으로 착각하면 안 됨.
        # 컴포넌트가 로드되면 자동으로 다시 실행되므로, 여기서는 잠깐 대기만 함.
        return "loading"
    token = cookies.get(COOKIE_NAME)
    if not token:
        return "none"
    user = db.get_session_user(token)
    if user:
        st.session_state["user"] = user
        st.session_state["_session_token"] = token
        return "restored"
    return "none"


def require_login():
    """
    로그인 안 되어 있으면 (쿠키로도 복원 안 되면) 로그인/가입 폼만 보여주고
    st.stop() 으로 페이지 실행을 막음.
    """
    _init_session()
    db.init_db()

    if st.session_state["user"] is None:
        status = _try_restore_session_from_cookie()
        if status == "loading":
            # 쿠키를 읽어오는 아주 짧은 순간. 빈 화면 대신 안내만 보여주고,
            # 쿠키 컴포넌트가 응답하면 스트림릿이 자동으로 다시 실행해줍니다.
            st.caption("불러오는 중…")
            st.stop()

    if st.session_state["user"] is not None:
        return st.session_state["user"]

    st.title("국장님 보고 진행현황")
    tab1, tab2 = st.tabs(["로그인", "최초 등록"])
    with tab1:
        _login_form()
    with tab2:
        _signup_form()
    st.stop()


def require_admin():
    """관리자 전용 페이지 맨 위에서 호출. 관리자가 아니면 안내만 보여주고 멈춤."""
    user = require_login()
    if user.get("role") != db.ROLE_ADMIN:
        st.warning("이 페이지는 관리자만 볼 수 있습니다.")
        st.stop()
    return user


def sidebar_user_info():
    user = current_user()
    if not user:
        return
    with st.sidebar:
        st.markdown(f"**{user['username']}**님 ({'관리자' if user['role'] == db.ROLE_ADMIN else '일반 사용자'})")
        if user.get("department"):
            st.caption(user["department"])
        if st.button("로그아웃", use_container_width=True):
            logout()
