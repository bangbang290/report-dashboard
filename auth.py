"""
auth.py
로그인 / 회원가입 폼과 세션 상태 관리.
각 페이지 맨 위에서 require_login() 을 호출하면 됩니다.

로그인은 이름 + 4자리 숫자 PIN 방식입니다. (짧아서 외우기 쉽지만, 그만큼 5회 연속
틀리면 5분간 잠기는 보호장치가 더 중요합니다 - db.py 의 로그인 시도 제한 참고)

로그인 유지(새로고침해도/페이지 이동해도/다른 앱 갔다와도 로그인 안 풀리게) 방식 (3번째 시도):
- 로그인 성공 시, 서버(DB)에 세션 토큰을 만들고 (1) 주소창 URL 뒤(?s=...)에 붙이고
  (2) 브라우저 쿠키에도 저장합니다.
- 이번엔 별도 외부 라이브러리(extra-streamlit-components) 없이, 스트림릿 자체 기능인
  st.components.v1.html() 로 순수 자바스크립트를 직접 심어서 쿠키를 다룹니다. 이전에
  쓰던 라이브러리는 자체 컴포넌트를 다른 위치에서 불러오는 구조라 그 통신 과정에서
  쿠키 저장이 자꾸 씹히는 문제가 있었던 것으로 보입니다. st.components.v1.html() 은
  스트림릿 앱과 같은 위치에서 바로 실행되는 훨씬 단순한 방식이라 더 안정적입니다.
- 흐름: 로그인 성공 → URL에 토큰 붙이고 + 쿠키에도 저장.
  이후 페이지 이동 등으로 URL의 토큰이 빠지면(이건 스트림릿 자체 특성) → 로그인 폼을
  보여주기 직전에, "쿠키에 토큰이 남아있으면 주소를 자동으로 그 토큰 붙은 걸로
  바꿔서 새로고침"하는 자바스크립트를 실행 → 새로고침된 페이지는 URL에 토큰이 있으니
  정상적으로 로그인 복원됨.
"""

import time

import streamlit as st
import streamlit.components.v1 as components

import db
from utils import validate_pin

COOKIE_NAME = "report_dashboard_session"
SESSION_QUERY_KEY = "s"


def _init_session():
    if "user" not in st.session_state:
        st.session_state["user"] = None


def current_user():
    _init_session()
    return st.session_state["user"]


def is_admin() -> bool:
    user = current_user()
    return bool(user) and user.get("role") == db.ROLE_ADMIN


def _set_browser_cookie(token: str):
    """순수 자바스크립트로 브라우저 쿠키에 로그인 토큰을 저장."""
    max_age_seconds = db.SESSION_MAX_AGE_DAYS * 24 * 60 * 60
    components.html(
        f"""
        <script>
        document.cookie = "{COOKIE_NAME}={token}; max-age={max_age_seconds}; path=/; SameSite=Lax";
        </script>
        """,
        height=0,
    )


def _clear_browser_cookie():
    components.html(
        f"""
        <script>
        document.cookie = "{COOKIE_NAME}=; max-age=0; path=/; SameSite=Lax";
        </script>
        """,
        height=0,
    )


def _sync_url_from_cookie():
    """
    (로그인 안 된 상태에서만 호출) 브라우저 쿠키에 로그인 토큰이 남아있는데 주소창에는
    없으면, 자바스크립트로 주소를 그 토큰이 붙은 형태로 바꿔서 자동으로 새로고침합니다.
    그러면 다음 페이지 로딩 때는 주소창의 토큰으로 정상 복원됩니다.
    """
    components.html(
        f"""
        <script>
        (function() {{
            try {{
                var topWin = window.top;
                var params = new URLSearchParams(topWin.location.search);
                if (!params.has('{SESSION_QUERY_KEY}')) {{
                    var match = document.cookie.match(/(?:^|; ){COOKIE_NAME}=([^;]*)/);
                    if (match && match[1]) {{
                        params.set('{SESSION_QUERY_KEY}', match[1]);
                        topWin.location.search = params.toString();
                    }}
                }}
            }} catch (e) {{}}
        }})();
        </script>
        """,
        height=0,
    )


def logout():
    token = st.session_state.get("_session_token")
    if token:
        db.delete_session(token)
    if SESSION_QUERY_KEY in st.query_params:
        del st.query_params[SESSION_QUERY_KEY]
    _clear_browser_cookie()
    time.sleep(0.3)
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
    st.query_params[SESSION_QUERY_KEY] = token
    _set_browser_cookie(token)
    st.session_state["user"] = result
    st.session_state["_session_token"] = token
    # 브라우저가 쿠키 저장 스크립트를 실제로 실행할 아주 짧은 시간을 줍니다.
    time.sleep(0.3)
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


def _try_restore_session_from_query():
    """주소창 URL에 로그인 토큰(?s=...)이 있으면 자동으로 로그인 상태를 복원."""
    token = st.query_params.get(SESSION_QUERY_KEY)
    if not token:
        return
    user = db.get_session_user(token)
    if user:
        st.session_state["user"] = user
        st.session_state["_session_token"] = token


def require_login():
    """
    로그인 안 되어 있으면 (URL 토큰으로도 복원 안 되면) 쿠키에서 자동 복구를 시도한 뒤,
    그래도 안 되면 로그인/가입 폼을 보여주고 st.stop() 으로 페이지 실행을 막음.
    """
    _init_session()
    db.init_db()

    if st.session_state["user"] is None:
        _try_restore_session_from_query()

    if st.session_state["user"] is not None:
        return st.session_state["user"]

    # 로그인 안 된 상태 - 혹시 브라우저 쿠키에 남아있는 토큰이 있으면, 주소를 그 토큰이
    # 붙은 형태로 자동으로 바꿔서 새로고침을 시도합니다. (성공하면 위 코드에서 복원됨)
    _sync_url_from_cookie()

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
