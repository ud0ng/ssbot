"""기본 시트의 설정 · 기록 탭.

설정 워크시트 (항목 | 값 | 설명) — 있으면 .env 값을 덮어쓴다. 서버에 들어가지 않고 시트에서 바꿀 수 있다.
  재화 이름        코인
  양도             사용 / 미사용
  출석 보상        1        (0 이면 [출석] 끔)
  툿 보상 기준     10       (툿 N개마다, 0 이면 끔)
  툿 보상 재화     1
  하루 조사 횟수   3        (0 이면 제한 없음)
  랜덤박스 명령어  랜덤박스
  키워드 이미지    사용 / 미사용
  운영진           manager, 아이디2   (.env 의 ADMIN_IDS 에 더해진다)

기록 워크시트 (시간 | 이름 | 아이디 | 종류 | 내용) — 있으면 구매·사용·양도 등을 한 줄씩 남긴다.
출석부 워크시트 (날짜 | 시간 | 이름 | 아이디) — 있으면 출석할 때마다 한 줄씩 남긴다.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Callable, Dict, Optional

from .config import Config, now_kst
from .sheets import Sheets, cell, header_index
from .text import normalize_key

logger = logging.getLogger(__name__)

SETTINGS = '설정'
RECORDS = '기록'
ATTENDANCE_BOOK = '출석부'

_TRUE = {'사용', '켜기', '켬', 'on', 'o', 'y', 'yes', 'true', '1', '예', '네'}
_FALSE = {'미사용', '끄기', '끔', 'off', 'x', 'n', 'no', 'false', '0', '아니오', '아니요'}


def _as_bool(value: str) -> Optional[bool]:
    key = normalize_key(value)
    if key in _TRUE:
        return True
    if key in _FALSE:
        return False
    return None


def _as_int(value: str) -> Optional[int]:
    digits = (value or '').replace(',', '').strip()
    try:
        return int(float(digits))
    except ValueError:
        return None


def _as_text(value: str) -> Optional[str]:
    return value.strip() or None


# 항목 이름(공백 무시) → (Config 필드, 변환)
FIELDS: Dict[str, tuple] = {
    '재화이름': ('currency', _as_text),
    '재화': ('currency', _as_text),
    '양도': ('transfer_enabled', _as_bool),
    '출석보상': ('attendance_reward', _as_int),
    '툿보상기준': ('toot_reward_every', _as_int),
    '툿보상재화': ('toot_reward_amount', _as_int),
    '하루조사횟수': ('investigation_daily_limit', _as_int),
    '조사횟수제한': ('investigation_daily_limit', _as_int),
    '랜덤박스명령어': ('randombox_command', _as_text),
    '키워드이미지': ('keyword_images', _as_bool),
}


def effective(base: Config, sheets: Sheets) -> Config:
    """설정 탭 값을 반영한 설정."""
    rows = sheets.rows_or_empty('main', SETTINGS)
    if not rows:
        return base
    try:
        header_row, columns = header_index(rows, '항목', '값')
    except LookupError:
        return base

    overrides = {}
    admins = list(base.admins)
    for row in rows[header_row + 1:]:
        key, value = normalize_key(cell(row, columns['항목'])), cell(row, columns['값'])
        if not key or value == '':
            continue
        if key in ('운영진', '운영진아이디', '관리자'):
            admins += [part.strip().lstrip('@') for part in value.replace('，', ',').split(',') if part.strip()]
            continue
        if key in FIELDS:
            name, parse = FIELDS[key]
            parsed = parse(value)
            if parsed is not None:
                overrides[name] = parsed
    return replace(base, admins=admins, **overrides)


class Recorder:
    """기록 · 출석부 탭에 한 줄씩 남긴다. 탭이 없으면 아무것도 하지 않는다."""

    def __init__(self, sheets: Sheets, clock: Callable = now_kst):
        self.sheets = sheets
        self.clock = clock

    def _append(self, worksheet: str, row: list) -> None:
        if not self.sheets.has('main'):
            return
        try:
            if worksheet in self.sheets.titles('main'):
                self.sheets.append('main', worksheet, [row])
        except Exception:
            logger.exception('%s 기록 실패', worksheet)

    def record(self, name: str, acct: str, kind: str, detail: str) -> None:
        self._append(RECORDS, [f'{self.clock():%Y-%m-%d %H:%M:%S}', name, acct, kind, detail])

    def attendance(self, name: str, acct: str) -> None:
        now = self.clock()
        self._append(ATTENDANCE_BOOK, [f'{now:%Y-%m-%d}', f'{now:%H:%M}', name, acct])
