"""
migrate_notion.py
노션에서 내보낸(Export) zip을 압축 해제한 폴더를 넣어주면,
"보고 진행현황 ..._all.csv" 와 "국장님 월간 일정 ..._all.csv" 를 찾아
report_dashboard.db 로 한 번에 옮겨주는 스크립트입니다.

사용법:
    python migrate_notion.py "노션_export_압축푼_폴더_경로"

* 이미 등록된 이름과 완전히 같은 항목이라도 중복 검사는 하지 않습니다.
  (노션 쪽 이력을 한 번만 옮기는 용도이므로, 여러 번 실행하면 중복 등록될 수 있습니다.
   재실행이 필요하면 report_dashboard.db 파일을 지우고 처음부터 다시 실행해주세요.)
* 가져온 데이터의 등록자(created_by)는 "노션가져오기"로 표시됩니다.
  이후 관리자 계정으로 로그인하면 각 항목을 자유롭게 수정/삭제할 수 있습니다.
"""

import csv
import glob
import os
import sys
from datetime import datetime

import db
from utils import parse_notion_datetime, parse_korean_date

IMPORTED_BY = "노션가져오기"


def find_csv(root: str, name_hint: str):
    pattern = os.path.join(root, "**", f"*{name_hint}*_all.csv")
    matches = glob.glob(pattern, recursive=True)
    return matches[0] if matches else None


def migrate_reports(csv_path: str):
    count = 0
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            team_name = (row.get("이름") or "").strip()
            status = (row.get("상태") or "시작 전").strip()
            if status not in db.STATUS_OPTIONS:
                status = "시작 전"
            raw_schedule = (row.get("📢보고 예정일") or "").strip()
            date_str, time_str = parse_notion_datetime(raw_schedule)

            memo_parts = []
            for col in ("담당자", "보고", "날짜", "국장님 보고 시작"):
                val = (row.get(col) or "").strip()
                if val:
                    memo_parts.append(f"{col}: {val}")
            memo = " / ".join(memo_parts)

            db.add_report(
                team_name=team_name,
                status=status,
                scheduled_date=date_str,
                scheduled_time=time_str,
                memo=memo,
                created_by=IMPORTED_BY,
                raw_schedule_text=raw_schedule,
                department="",
                team_detail=team_name,
            )
            count += 1
    return count


def migrate_schedule(csv_path: str):
    count = 0
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            event_name = (row.get("이름") or "").strip()
            raw_date = (row.get("날짜") or "").strip()
            if not event_name:
                continue
            date_str = parse_korean_date(raw_date)
            if not date_str:
                date_str, _ = parse_notion_datetime(raw_date)
            if not date_str:
                # 파싱 실패한 날짜는 오늘 날짜로 넣지 않고 건너뜀 (수동 확인 필요)
                print(f"  [건너뜀] 날짜 파싱 실패: {event_name!r} / {raw_date!r}")
                continue
            db.add_schedule(event_name, date_str, IMPORTED_BY)
            count += 1
    return count


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    root = sys.argv[1]
    if not os.path.isdir(root):
        print(f"폴더를 찾을 수 없습니다: {root}")
        sys.exit(1)

    db.init_db()

    reports_csv = find_csv(root, "보고 진행현황")
    schedule_csv = find_csv(root, "국장님 월간 일정")

    if reports_csv:
        n = migrate_reports(reports_csv)
        print(f"✅ 보고 진행현황 {n}건 가져오기 완료 ({reports_csv})")
    else:
        print("⚠️  '보고 진행현황 ..._all.csv' 파일을 찾지 못했습니다.")

    if schedule_csv:
        n = migrate_schedule(schedule_csv)
        print(f"✅ 국장님 월간 일정 {n}건 가져오기 완료 ({schedule_csv})")
    else:
        print("⚠️  '국장님 월간 일정 ..._all.csv' 파일을 찾지 못했습니다.")

    print(f"\nDB 파일 위치: {db.DB_PATH}")


if __name__ == "__main__":
    main()
