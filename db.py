"""
db.py
SQLite / Turso 데이터베이스 연결 및 스키마/CRUD 헬퍼 모음.

저장소는 두 가지 모드로 동작합니다:
- 로컬 모드 (기본값): 이 파일과 같은 폴더의 report_dashboard.db 파일 하나를 사용.
  스트림릿 클라우드 시크릿(TURSO_DATABASE_URL / TURSO_AUTH_TOKEN)이 없거나, 연결에
  실패하면 자동으로 이 모드로 동작합니다. 다만 스트림릿 클라우드는 12시간 동안 접속이
  없으면 앱이 잠들었다가 깨어날 때 이 로컬 파일이 초기화될 수 있습니다.
- Turso 모드: 위 두 시크릿이 설정되어 있고 연결에 성공하면 자동으로 전환됩니다.
  ⚠️ TURSO_DATABASE_URL은 지역 코드(예: aws-ap-northeast-1)가 안 붙은 기본 주소
  (libsql://데이터베이스이름-계정이름.turso.io) 를 써야 합니다. 지역 코드가 붙은 주소는
  일부 환경에서 웹소켓 연결이 막혀서 접속이 안 되는 경우가 있었습니다.
  혹시 연결에 실패하더라도 앱이 죽지 않고 자동으로 로컬 모드로 넘어가도록 만들어뒀습니다
  (init_db() 에서 연결을 실제로 테스트해보고, 안 되면 조용히 전환합니다).

동시 접속 관련 설계:
- WAL 모드 사용(로컬 모드에서만 해당): 한 사람이 쓰는 동안 다른 사람이 읽는 것까지
  막히지 않도록 함.
- busy_timeout 설정: 아주 짧은 순간 여러 명이 동시에 쓰려고 하면, 에러를 즉시 던지지 않고
  최대 5초까지 기다렸다가 처리함.
- register_report / edit_report 는 "겹치는 시간 확인 + 실제 저장"을 하나의 트랜잭션(BEGIN IMMEDIATE
  / Turso 트랜잭션)으로 묶어서, 두 사람이 동시에 같은 시간대를 등록해도 한 명만 성공하도록 함.
  (Turso 모드에서 트랜잭션 자체가 지원 안 되는 경우, 그 부분만 안전하게 대체 처리합니다.)

상태 자동 전환 설계:
- 보고 등록 시에는 항상 '시작 전' 으로 시작합니다 (사람이 직접 상태를 고르지 않음).
- auto_update_statuses() 를 화면을 열 때마다 호출해서, 예정 시각이 지나면 자동으로
  '진행 중' -> (20분 경과) '완료' 로 전진시킵니다. 시각을 사람이 직접 수정하면(edit_report)
  그 즉시 새 시각 기준으로 양방향(앞으로/뒤로) 재계산됩니다.

시간대 관련:
- 스트림릿 클라우드 등 배포 서버는 한국 시간이 아닐 수 있어서(보통 UTC), "지금 몇 시인지"는
  항상 _now_kst() 로 한국 시간 기준으로 강제 계산합니다.
"""

import sqlite3
import hashlib
import os
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from contextlib import contextmanager

from utils import find_conflicts, suggest_next_available_time, compose_team_name

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_dashboard.db")

# 서버가 어느 시간대에서 돌든(스트림릿 클라우드는 보통 UTC) 상관없이,
# "지금 몇 시인지"는 항상 한국 시간(KST) 기준으로 계산합니다.
KST = ZoneInfo("Asia/Seoul")


def _now_kst():
    """현재 시각을 한국 시간 기준으로. (서버가 UTC든 어디든 상관없이 항상 KST 기준 시각을 반환)"""
    return datetime.now(KST).replace(tzinfo=None)


STATUS_OPTIONS = ["시작 전", "진행 중", "완료"]
STATUS_ORDER = {"시작 전": 0, "진행 중": 1, "완료": 2}
STATUS_AUTO_COMPLETE_MINUTES = 20  # 예정 시각으로부터 이만큼 지나면 자동 완료 처리

ROLE_ADMIN = "admin"
ROLE_USER = "user"

SESSION_MAX_AGE_DAYS = 30
LOGIN_MAX_FAILS = 5
LOGIN_LOCK_MINUTES = 5

DEPARTMENT_OPTIONS = [
    "심사총괄2과",
    "심사1관", "심사2관", "심사3관", "심사4관", "심사5관", "심사6관", "심사7관",
    "FTA검증1과", "FTA검증2과", "FTA검증3과",
    "기타(직접입력)",
]


def _now():
    return _now_kst().isoformat(timespec="seconds")


# ========== 저장소 모드 판단 (로컬 SQLite vs Turso) ==========

def _get_secret(name):
    """환경변수 먼저 보고, 없으면 스트림릿 시크릿(st.secrets)에서 찾음. 둘 다 없으면 None."""
    val = os.environ.get(name)
    if val:
        return val
    try:
        import streamlit as st
        return st.secrets.get(name)
    except Exception:
        return None


TURSO_DATABASE_URL = _get_secret("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = _get_secret("TURSO_AUTH_TOKEN")

# 실제로 시크릿이 설정되어 있고 "연결도 성공했을 때"만 True 로 바뀝니다.
# (init_db() 에서 실제로 연결 테스트를 해보고 결정 - 아래 _ensure_storage_mode_decided 참고)
USE_TURSO = False
_storage_mode_decided = False

_turso_client = None


def _get_turso_client():
    global _turso_client
    if _turso_client is None:
        import libsql_client
        _turso_client = libsql_client.create_client_sync(
            url=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN
        )
    return _turso_client


def _ensure_storage_mode_decided():
    """
    앱이 시작될 때 딱 한 번, Turso 시크릿이 있으면 실제로 접속을 테스트해봅니다.
    성공하면 USE_TURSO=True, 실패하면(또는 시크릿이 없으면) 조용히 로컬 모드로 남고
    화면에 안내만 한 번 띄워줍니다. 이 판단 이후로는 다시 테스트하지 않습니다
    (매 요청마다 접속 테스트를 하면 느려지므로).
    """
    global USE_TURSO, _storage_mode_decided
    if _storage_mode_decided:
        return
    _storage_mode_decided = True

    if not (TURSO_DATABASE_URL and TURSO_AUTH_TOKEN):
        return  # 시크릿 자체가 없음 -> 로컬 모드

    try:
        client = _get_turso_client()
        client.execute("SELECT 1")
        USE_TURSO = True
    except Exception as e:
        USE_TURSO = False
        try:
            import streamlit as st
            st.warning(
                f"⚠️ Turso(영구 저장소) 연결에 실패해서, 이번 실행은 로컬 저장 방식으로 동작합니다. "
                f"이 경우 앱이 재배포되거나 오래 쉬면 데이터가 초기화될 수 있습니다.\n\n"
                f"**오류 종류**: `{type(e).__name__}`\n\n"
                f"**상세 내용**: `{str(e)}`"
            )
        except Exception:
            pass


class _TursoCursor:
    """libsql_client 의 실행 결과를 sqlite3 커서처럼(.fetchone/.fetchall) 흉내내는 래퍼.
    fetchall()이 이미 일반 dict를 반환하므로, 기존 코드의 dict(row) 호출은 dict(dict)로
    그냥 복사만 되어 문제없이 동작합니다."""

    def __init__(self, result_set):
        columns = list(getattr(result_set, "columns", []) or [])
        self._rows = [dict(zip(columns, row)) for row in result_set.rows]
        self._idx = 0
        self.lastrowid = (
            getattr(result_set, "last_insert_rowid", None)
            or getattr(result_set, "last_insert_row_id", None)
        )

    def fetchone(self):
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
            self._idx += 1
            return row
        return None

    def fetchall(self):
        rows = self._rows[self._idx:]
        self._idx = len(self._rows)
        return rows


class _TursoConn:
    """db.py 나머지 코드가 기대하는 최소 인터페이스(.execute/.commit/.rollback/.close)로
    libsql_client 를 감싸는 어댑터. tx가 주어지면 그 트랜잭션 범위 안에서 실행.
    (연결의 트랜잭션 API가 없는 경우엔 tx 없이 그냥 개별 실행 - 완벽한 원자성은 아니지만
    앱이 죽는 것보다는 낫습니다.)"""

    def __init__(self, client, tx=None):
        self._client = client
        self._tx = tx

    def execute(self, sql, params=None):
        target = self._tx if self._tx is not None else self._client
        rs = target.execute(sql, params or [])
        return _TursoCursor(rs)

    def commit(self):
        if self._tx is not None:
            self._tx.commit()

    def rollback(self):
        if self._tx is not None:
            self._tx.rollback()

    def close(self):
        pass  # 클라이언트는 프로세스 전체에서 재사용, 개별 연결 단위로 닫지 않음


@contextmanager
def get_conn():
    _ensure_storage_mode_decided()
    if USE_TURSO:
        client = _get_turso_client()
        conn = _TursoConn(client)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn_immediate():
    """
    쓰기 작업 하나를 '한 명씩 순서대로'만 처리하도록 즉시 쓰기 락을 거는 트랜잭션.
    같은 시간대 중복 등록 같은 동시 접속 문제를 막기 위해 사용.
    """
    _ensure_storage_mode_decided()
    if USE_TURSO:
        client = _get_turso_client()
        try:
            tx = client.transaction()
        except Exception:
            tx = None  # 이 연결 방식이 트랜잭션을 지원 안 하면, tx 없이 개별 실행으로 대체
        conn = _TursoConn(client, tx=tx)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _table_columns(conn, table_name):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def init_db():
    """최초 실행 시 테이블이 없으면 생성. 이미 있으면 부족한 컬럼만 추가(스키마 업그레이드)."""
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                department TEXT,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '시작 전',
                scheduled_date TEXT,
                scheduled_time TEXT,
                raw_schedule_text TEXT,
                memo TEXT,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # 스키마 업그레이드: department / team_detail 컬럼이 없으면 추가
        cols = _table_columns(conn, "reports")
        if "department" not in cols:
            conn.execute("ALTER TABLE reports ADD COLUMN department TEXT")
        if "team_detail" not in cols:
            conn.execute("ALTER TABLE reports ADD COLUMN team_detail TEXT")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS monthly_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_name TEXT NOT NULL,
                event_date TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                record_id INTEGER,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS login_attempts (
                username TEXT PRIMARY KEY,
                fail_count INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT
            )
            """
        )


# ---------- 활동 이력 (감사로그) ----------

def log_activity(table_name: str, record_id, action: str, actor: str, detail: str = "", conn=None):
    row = (table_name, record_id, action, actor, detail, _now())
    if conn is not None:
        conn.execute(
            "INSERT INTO activity_log (table_name, record_id, action, actor, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            row,
        )
    else:
        with get_conn() as c:
            c.execute(
                "INSERT INTO activity_log (table_name, record_id, action, actor, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                row,
            )


def list_activity(limit: int = 200):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ---------- 비밀번호 해시 ----------

def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def create_user(username: str, password: str, department: str = "", role: str = ROLE_USER):
    username = username.strip()
    if not username or not password:
        return False, "이름과 비밀번호를 모두 입력해주세요."
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(password, salt)
    now = _now()
    try:
        with get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, department, password_hash, salt, role, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (username, department, pw_hash, salt, role, now),
            )
            log_activity("users", cur.lastrowid, "create", username, f"계정 생성 (역할: {role})", conn=conn)
        return True, "계정이 생성되었습니다."
    except sqlite3.IntegrityError:
        return False, "이미 존재하는 이름입니다. 다른 이름을 사용하거나 로그인해주세요."


def _is_locked_out(conn, username: str):
    row = conn.execute("SELECT * FROM login_attempts WHERE username = ?", (username,)).fetchone()
    if not row or not row["locked_until"]:
        return False, None
    locked_until = datetime.fromisoformat(row["locked_until"])
    if _now_kst() < locked_until:
        return True, locked_until
    return False, None


def verify_user(username: str, password: str):
    username = username.strip()
    with get_conn() as conn:
        locked, until = _is_locked_out(conn, username)
        if locked:
            return {"__locked_until__": until.strftime("%H:%M:%S")}

        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        ok = row is not None and _hash_password(password, row["salt"]) == row["password_hash"]

        if ok:
            conn.execute("DELETE FROM login_attempts WHERE username = ?", (username,))
            return dict(row)
        else:
            existing = conn.execute("SELECT * FROM login_attempts WHERE username = ?", (username,)).fetchone()
            fail_count = (existing["fail_count"] if existing else 0) + 1
            locked_until = None
            if fail_count >= LOGIN_MAX_FAILS:
                locked_until = (_now_kst() + timedelta(minutes=LOGIN_LOCK_MINUTES)).isoformat()
                fail_count = 0
            if existing:
                conn.execute(
                    "UPDATE login_attempts SET fail_count = ?, locked_until = ? WHERE username = ?",
                    (fail_count, locked_until, username),
                )
            else:
                conn.execute(
                    "INSERT INTO login_attempts (username, fail_count, locked_until) VALUES (?, ?, ?)",
                    (username, fail_count, locked_until),
                )
            return None


def user_exists(username: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM users WHERE username = ?", (username.strip(),)).fetchone()
    return row is not None


def any_admin_exists() -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM users WHERE role = ?", (ROLE_ADMIN,)).fetchone()
    return row is not None


def list_users():
    with get_conn() as conn:
        rows = conn.execute("SELECT id, username, department, role, created_at FROM users ORDER BY username").fetchall()
    return [dict(r) for r in rows]


def get_user_by_id(user_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def count_admins() -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM users WHERE role = ?", (ROLE_ADMIN,)).fetchone()
    return row["c"]


def set_user_role(user_id: int, new_role: str, actor: str):
    user = get_user_by_id(user_id)
    if not user:
        return False, "존재하지 않는 사용자입니다."
    if user["role"] == ROLE_ADMIN and new_role != ROLE_ADMIN and count_admins() <= 1:
        return False, "관리자가 최소 1명은 있어야 합니다. 다른 사람을 먼저 관리자로 지정한 뒤 강등해주세요."
    with get_conn() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        log_activity(
            "users", user_id, "role_change", actor,
            f"{user['username']} 의 역할을 {user['role']} → {new_role} 로 변경", conn=conn,
        )
    return True, "역할이 변경되었습니다."


def delete_user(user_id: int, actor: str):
    user = get_user_by_id(user_id)
    if not user:
        return False, "존재하지 않는 사용자입니다."
    if user["role"] == ROLE_ADMIN and count_admins() <= 1:
        return False, "관리자가 최소 1명은 있어야 합니다. 다른 사람을 먼저 관리자로 지정한 뒤 삭제해주세요."
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.execute("DELETE FROM sessions WHERE username = ?", (user["username"],))
        conn.execute("DELETE FROM login_attempts WHERE username = ?", (user["username"],))
        log_activity("users", user_id, "delete", actor, f"{user['username']} 계정 삭제", conn=conn)
    return True, "계정이 삭제되었습니다."


def reset_password(user_id: int, new_password: str, actor: str):
    user = get_user_by_id(user_id)
    if not user:
        return False, "존재하지 않는 사용자입니다."
    salt = secrets.token_hex(16)
    pw_hash = _hash_password(new_password, salt)
    with get_conn() as conn:
        conn.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?", (pw_hash, salt, user_id))
        conn.execute("DELETE FROM sessions WHERE username = ?", (user["username"],))
        conn.execute("DELETE FROM login_attempts WHERE username = ?", (user["username"],))
        log_activity("users", user_id, "password_reset", actor, f"{user['username']} 비밀번호 초기화", conn=conn)
    return True, "비밀번호가 초기화되었습니다."


# ---------- 로그인 세션 유지 (쿠키용 토큰) ----------

def create_session(username: str) -> str:
    token = secrets.token_hex(32)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, username, created_at) VALUES (?, ?, ?)",
            (token, username, _now()),
        )
    return token


def get_session_user(token: str):
    if not token:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
        if not row:
            return None
        created_at = datetime.fromisoformat(row["created_at"])
        if _now_kst() - created_at > timedelta(days=SESSION_MAX_AGE_DAYS):
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return None
        user_row = conn.execute("SELECT * FROM users WHERE username = ?", (row["username"],)).fetchone()
    return dict(user_row) if user_row else None


def delete_session(token: str):
    if not token:
        return
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


# ---------- 보고 진행현황 (reports) ----------

def add_report(team_name, status, scheduled_date, scheduled_time, memo, created_by,
               raw_schedule_text="", department="", team_detail=""):
    """저장만 하는 저수준 함수 (충돌 체크 없음). 마이그레이션 스크립트 전용으로 남겨둠."""
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO reports
                (team_name, status, scheduled_date, scheduled_time, raw_schedule_text, memo,
                 created_by, created_at, updated_at, department, team_detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (team_name, status, scheduled_date, scheduled_time, raw_schedule_text, memo,
             created_by, now, now, department, team_detail),
        )


def get_times_for_date(date_str: str, exclude_id: int = None, conn=None):
    if not date_str:
        return []
    query = "SELECT id, team_name, scheduled_time FROM reports WHERE scheduled_date = ? AND scheduled_time IS NOT NULL"
    params = [date_str]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    if conn is not None:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    with get_conn() as c:
        rows = c.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def _compute_status_for_schedule(scheduled_date, scheduled_time, now=None):
    """예정 날짜/시각을 기준으로 지금 이 순간의 '올바른' 상태를 계산."""
    now = now or _now_kst()
    if not scheduled_date or not scheduled_time:
        return "시작 전"
    try:
        sched_dt = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        return "시작 전"

    if now >= sched_dt + timedelta(minutes=STATUS_AUTO_COMPLETE_MINUTES):
        return "완료"
    elif now >= sched_dt:
        return "진행 중"
    else:
        return "시작 전"


def register_report(department, team_detail, scheduled_date, scheduled_time, memo, created_by,
                     min_gap=20, override=False):
    """
    새 보고 등록. 상태는 항상 '시작 전'으로 시작합니다 (자동 전환은 auto_update_statuses 가 처리).
    겹치는 시간 확인 + 저장을 하나의 트랜잭션으로 묶어서, 동시에 여러 명이 같은 시간대를
    등록해도 한쪽만 성공하도록 함.
    반환: (성공여부, 충돌목록, 추천시간)
    """
    team_name = compose_team_name(department, team_detail)
    with get_conn_immediate() as conn:
        if scheduled_date and scheduled_time and not override:
            existing = get_times_for_date(scheduled_date, conn=conn)
            conflicts = find_conflicts(existing, scheduled_time, min_gap)
            if conflicts:
                suggested = suggest_next_available_time(existing, scheduled_time, min_gap)
                return False, conflicts, suggested

        now = _now()
        cur = conn.execute(
            """
            INSERT INTO reports
                (team_name, status, scheduled_date, scheduled_time, raw_schedule_text, memo,
                 created_by, created_at, updated_at, department, team_detail)
            VALUES (?, '시작 전', ?, ?, '', ?, ?, ?, ?, ?, ?)
            """,
            (team_name, scheduled_date, scheduled_time, memo, created_by, now, now, department, team_detail),
        )
        log_activity(
            "reports", cur.lastrowid, "create", created_by,
            f"'{team_name}' 등록 (예정: {scheduled_date or '-'} {scheduled_time or ''})",
            conn=conn,
        )
        return True, [], None


def edit_report(report_id, department, team_detail, scheduled_date, scheduled_time, memo, actor,
                 min_gap=20, override=False):
    """기존 보고 항목 수정 (상태는 자동 전환 로직이 따로 관리하므로 여기서는 건드리지 않음)."""
    team_name = compose_team_name(department, team_detail)
    with get_conn_immediate() as conn:
        old_row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not old_row:
            return False, [], None

        if scheduled_date and scheduled_time and not override:
            existing = get_times_for_date(scheduled_date, exclude_id=report_id, conn=conn)
            conflicts = find_conflicts(existing, scheduled_time, min_gap)
            if conflicts:
                suggested = suggest_next_available_time(existing, scheduled_time, min_gap)
                return False, conflicts, suggested

        now = _now()
        # 날짜/시각이 바뀌었을 수 있으므로, 수정하는 이 시점 기준으로 상태를 완전히 다시 계산
        # (자동 전환과 달리, 사람이 직접 시각을 고친 경우이므로 앞으로도/뒤로도 정확히 맞춰줌)
        new_status = _compute_status_for_schedule(scheduled_date, scheduled_time)

        conn.execute(
            """
            UPDATE reports
            SET team_name = ?, status = ?, scheduled_date = ?, scheduled_time = ?, memo = ?, updated_at = ?,
                department = ?, team_detail = ?
            WHERE id = ?
            """,
            (team_name, new_status, scheduled_date, scheduled_time, memo, now, department, team_detail, report_id),
        )

        changes = []
        old = dict(old_row)
        new = {
            "team_name": team_name, "status": new_status,
            "scheduled_date": scheduled_date, "scheduled_time": scheduled_time, "memo": memo,
        }
        for field, label in [
            ("team_name", "이름"), ("status", "상태"),
            ("scheduled_date", "날짜"), ("scheduled_time", "시각"), ("memo", "비고"),
        ]:
            if (old.get(field) or "") != (new.get(field) or ""):
                changes.append(f"{label}: '{old.get(field) or ''}' → '{new.get(field) or ''}'")
        detail = "; ".join(changes) if changes else "(변경 없음)"
        log_activity("reports", report_id, "update", actor, detail, conn=conn)
        return True, [], None


def remove_report(report_id, actor):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            return
        conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))
        log_activity("reports", report_id, "delete", actor, f"'{row['team_name']}' 삭제됨", conn=conn)


def list_reports(status_filter=None, order_by="scheduled_date"):
    query = "SELECT * FROM reports"
    params = ()
    if status_filter and status_filter != "전체":
        query += " WHERE status = ?"
        params = (status_filter,)
    query += f" ORDER BY {order_by} IS NULL, {order_by} ASC, scheduled_time ASC"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_report(report_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    return dict(row) if row else None


def auto_update_statuses():
    """
    예정 시각이 지난 '시작 전' 건은 '진행 중'으로, 그로부터 20분 더 지나면 '완료'로 자동 전환.
    (시간은 항상 앞으로만 흐르므로 이 자동 전환도 항상 앞으로만 이동합니다. 사람이 직접
    시각을 수정하는 경우의 상태 재계산은 edit_report 에서 즉시, 양방향으로 처리합니다.)
    화면을 열 때마다 호출하면 됩니다 (앱/보고 진행현황 페이지 상단에서 호출).
    """
    now = _now_kst()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, status, scheduled_date, scheduled_time FROM reports WHERE status != '완료'"
        ).fetchall()
        for r in rows:
            if not r["scheduled_date"] or not r["scheduled_time"]:
                continue

            target_status = _compute_status_for_schedule(r["scheduled_date"], r["scheduled_time"], now=now)

            if STATUS_ORDER.get(target_status, 0) > STATUS_ORDER.get(r["status"], 0):
                conn.execute(
                    "UPDATE reports SET status = ?, updated_at = ? WHERE id = ?",
                    (target_status, now.isoformat(timespec="seconds"), r["id"]),
                )
                log_activity(
                    "reports", r["id"], "auto_status", "system",
                    f"{r['status']} → {target_status} (예정시각 기준 자동 전환)", conn=conn,
                )


# ---------- 국장님 월간 일정 (monthly_schedule) ----------

def add_schedule(event_name, event_date, created_by):
    """저장만 하는 저수준 함수 (마이그레이션 스크립트 전용)."""
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO monthly_schedule (event_name, event_date, created_by, created_at) VALUES (?, ?, ?, ?)",
            (event_name, event_date, created_by, now),
        )


def register_schedule(event_name, event_date, created_by):
    now = _now()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO monthly_schedule (event_name, event_date, created_by, created_at) VALUES (?, ?, ?, ?)",
            (event_name, event_date, created_by, now),
        )
        log_activity("monthly_schedule", cur.lastrowid, "create", created_by, f"'{event_name}' ({event_date}) 등록", conn=conn)


def edit_schedule(schedule_id, event_name, event_date, actor):
    with get_conn() as conn:
        old_row = conn.execute("SELECT * FROM monthly_schedule WHERE id = ?", (schedule_id,)).fetchone()
        if not old_row:
            return
        conn.execute(
            "UPDATE monthly_schedule SET event_name = ?, event_date = ? WHERE id = ?",
            (event_name, event_date, schedule_id),
        )
        detail = f"'{old_row['event_name']}'({old_row['event_date']}) → '{event_name}'({event_date})"
        log_activity("monthly_schedule", schedule_id, "update", actor, detail, conn=conn)


def remove_schedule(schedule_id, actor):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM monthly_schedule WHERE id = ?", (schedule_id,)).fetchone()
        if not row:
            return
        conn.execute("DELETE FROM monthly_schedule WHERE id = ?", (schedule_id,))
        log_activity("monthly_schedule", schedule_id, "delete", actor, f"'{row['event_name']}' 삭제됨", conn=conn)


def list_schedule(month=None):
    """month: 'YYYY-MM' 형식이면 해당 월만 필터링"""
    query = "SELECT * FROM monthly_schedule"
    params = ()
    if month:
        query += " WHERE event_date LIKE ?"
        params = (f"{month}%",)
    query += " ORDER BY event_date ASC"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_schedule_item(schedule_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM monthly_schedule WHERE id = ?", (schedule_id,)).fetchone()
    return dict(row) if row else None
