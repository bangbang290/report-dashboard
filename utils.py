"""
utils.py
노션에서 흔히 보이던 날짜 문자열들을 표준 형식으로 변환/파싱하는 유틸.
- "27/03/2026 16:00 (GMT+9)"        -> date=2026-03-27, time=16:00
- "27/03/2026 16:00 (GMT+9) → 14:30" -> date=2026-03-27, time=16:00 (종료시각은 memo로)
- "27/03/2026"                       -> date=2026-03-27, time=None
- "2026년 4월 3일"                    -> date=2026-04-03
"""

import re
from datetime import datetime, date

REPORT_TIME_MIN = "09:30"
REPORT_TIME_MAX = "18:00"
REPORT_TIME_STEP_MINUTES = 10


def parse_notion_datetime(text: str):
    """
    반환: (date_str 'YYYY-MM-DD' 또는 None, time_str 'HH:MM' 또는 None)
    """
    if not text:
        return None, None
    text = text.strip()
    if not text:
        return None, None

    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", text)
    if m:
        day, month, year, hour, minute = m.groups()
        try:
            d = date(int(year), int(month), int(day))
        except ValueError:
            return None, None
        date_str = d.isoformat()
        time_str = f"{int(hour):02d}:{minute}" if hour is not None else None
        return date_str, time_str

    return None, None


def parse_korean_date(text: str):
    """'2026년 4월 3일' -> '2026-04-03'"""
    if not text:
        return None
    m = re.match(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", text.strip())
    if not m:
        return None
    year, month, day = m.groups()
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return None


def format_date_kr(date_str: str) -> str:
    """'2026-04-03' -> '2026년 4월 3일'"""
    if not date_str:
        return ""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return f"{d.year}년 {d.month}월 {d.day}일"
    except ValueError:
        return date_str


def format_schedule_display(date_str: str, time_str: str) -> str:
    if not date_str:
        return "(예정일 미정)"
    disp = format_date_kr(date_str)
    if time_str:
        disp += f" {time_str}"
    return disp


# ---------- 팀명 조합 (과 + 팀/보고자) ----------

def compose_team_name(department: str, team_detail: str) -> str:
    parts = [p.strip() for p in [department, team_detail] if p and p.strip()]
    return " ".join(parts)


# ---------- 월간 일정 이름에서 시간 분리/조합 (예: "(11:00) 확대간부회의") ----------

def parse_event_name(event_name: str):
    """'(11:00) 확대간부회의' -> ('11:00', '확대간부회의'). 시간이 없으면 (None, 원래이름)."""
    event_name = event_name or ""
    m = re.match(r"^\((\d{1,2}:\d{2})\)\s*(.*)$", event_name.strip())
    if m:
        return m.group(1), m.group(2)
    return None, event_name.strip()


def compose_event_name(time_str: str, title: str) -> str:
    title = (title or "").strip()
    if time_str:
        return f"({time_str}) {title}".strip()
    return title


# ---------- 로그인 PIN 검증 (4자리 숫자) ----------

def validate_pin(text: str):
    """반환: (유효여부, 에러메시지)"""
    text = (text or "").strip()
    if not text:
        return False, "PIN을 입력해주세요."
    if not (text.isdigit() and len(text) == 4):
        return False, "PIN은 숫자 4자리여야 합니다. (예: 1234)"
    return True, ""


# ---------- 보고 예정 시각 검증 (09:30~18:00, 수기 입력) ----------

def validate_time_str(text: str):
    """반환: (유효여부, 에러메시지)"""
    text = (text or "").strip()
    if not text:
        return False, "보고 예정 시각을 입력해주세요. (예: 14:20)"
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", text):
        return False, "시간 형식이 올바르지 않습니다. HH:MM 형식으로 입력해주세요. (예: 14:20)"
    if not (REPORT_TIME_MIN <= text <= REPORT_TIME_MAX):
        return False, f"보고 시간은 {REPORT_TIME_MIN} ~ {REPORT_TIME_MAX} 사이로만 등록할 수 있습니다."
    return True, ""


# ---------- 20분 간격 체크 (같은 날짜 내 보고 시간 겹침 방지) ----------

def time_to_minutes(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def minutes_to_time(m: int) -> str:
    m = m % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def find_conflicts(existing: list, desired_time: str, min_gap: int = 20) -> list:
    """
    existing: [{'id':.., 'team_name':.., 'scheduled_time': 'HH:MM'}, ...]
    desired_time과 min_gap분 미만으로 붙어있는 항목들을 반환 (시간순 정렬).
    """
    if not desired_time:
        return []
    dm = time_to_minutes(desired_time)
    conflicts = []
    for e in existing:
        t = e.get("scheduled_time")
        if not t:
            continue
        if abs(time_to_minutes(t) - dm) < min_gap:
            conflicts.append(e)
    return sorted(conflicts, key=lambda e: e["scheduled_time"])


def suggest_next_available_time(existing: list, desired_time: str, min_gap: int = 20, max_iter: int = 200) -> str:
    """desired_time부터 시작해서 min_gap분 간격을 만족하는 가장 이른 시간을 찾아 반환."""
    candidate = desired_time
    for _ in range(max_iter):
        conflicts = find_conflicts(existing, candidate, min_gap)
        if not conflicts:
            return candidate
        latest_conflict_minutes = max(time_to_minutes(c["scheduled_time"]) for c in conflicts)
        candidate = minutes_to_time(latest_conflict_minutes + min_gap)
    return candidate


# ---------- 보고 예정 시각 드롭다운 옵션 (09:30~18:00, 10분 간격) ----------

def generate_time_options(start: str = REPORT_TIME_MIN, end: str = REPORT_TIME_MAX,
                           step_minutes: int = REPORT_TIME_STEP_MINUTES) -> list:
    start_m = time_to_minutes(start)
    end_m = time_to_minutes(end)
    return [minutes_to_time(m) for m in range(start_m, end_m + 1, step_minutes)]


REPORT_TIME_OPTIONS = generate_time_options()

# 월간 일정용 시간 목록 (출퇴근/외출 등도 있어서 더 넓은 범위로)
SCHEDULE_TIME_OPTIONS = generate_time_options("06:00", "22:00", 10)
