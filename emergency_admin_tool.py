"""
emergency_admin_tool.py
관리자 전원이 PIN을 잊어버리는 등, 앱 화면 안에서는 관리자 기능(역할 변경, PIN 초기화)에
접근할 방법이 없는 '비상 상황'에서만 쓰는 커맨드라인 도구입니다.

이 폴더(report_dashboard.db 가 있는 서버/PC)에 직접 접근할 수 있는 사람만 실행할 수 있으므로,
IT담당자나 비서실 등 신뢰할 수 있는 사람만 사용해야 합니다.

사용법 (터미널/명령 프롬프트에서 이 폴더 안에 들어와 실행):
    python emergency_admin_tool.py list
        -> 전체 사용자와 역할(관리자/일반) 목록 보기

    python emergency_admin_tool.py promote 홍길동
        -> '홍길동' 계정을 관리자로 승격

    python emergency_admin_tool.py reset-pin 홍길동 1234
        -> '홍길동' 계정의 PIN을 1234로 강제 초기화 (기존 로그인 세션도 모두 무효화됨)
"""

import sys

import db


def _find_user(username):
    users = {u["username"]: u for u in db.list_users()}
    return users.get(username)


def cmd_list():
    users = db.list_users()
    if not users:
        print("등록된 사용자가 없습니다.")
        return
    print(f"{'ID':>4} | {'이름':<15} | {'역할':<6} | 부서")
    print("-" * 50)
    for u in users:
        role_label = "관리자" if u["role"] == db.ROLE_ADMIN else "일반"
        print(f"{u['id']:>4} | {u['username']:<15} | {role_label:<6} | {u['department'] or '-'}")


def cmd_promote(username):
    user = _find_user(username)
    if not user:
        print(f"'{username}' 계정을 찾을 수 없습니다. 'python emergency_admin_tool.py list' 로 정확한 이름을 확인해주세요.")
        return
    ok, msg = db.set_user_role(user["id"], db.ROLE_ADMIN, actor="emergency_admin_tool")
    print(msg)


def cmd_reset_pin(username, new_pin):
    if not (new_pin.isdigit() and len(new_pin) == 4):
        print("PIN은 숫자 4자리여야 합니다. 예: 1234")
        return
    user = _find_user(username)
    if not user:
        print(f"'{username}' 계정을 찾을 수 없습니다. 'python emergency_admin_tool.py list' 로 정확한 이름을 확인해주세요.")
        return
    ok, msg = db.reset_password(user["id"], new_pin, actor="emergency_admin_tool")
    print(msg)


def main():
    db.init_db()
    args = sys.argv[1:]

    if len(args) == 1 and args[0] == "list":
        cmd_list()
    elif len(args) == 2 and args[0] == "promote":
        cmd_promote(args[1])
    elif len(args) == 3 and args[0] == "reset-pin":
        cmd_reset_pin(args[1], args[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
