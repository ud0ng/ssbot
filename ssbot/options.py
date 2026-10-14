"""기본 시트의 설정 탭.

설정 워크시트 (항목 | 값 | 설명) — 있으면 .env 값을 덮어쓴다. 서버에 들어가지 않고 시트에서 바꿀 수 있다.
  하루 조사 횟수   3        (0 이면 제한 없음)
  랜덤박스 명령어  랜덤박스
  키워드 이미지    사용 / 미사용
  운영진           manager, 아이디2   (.env 의 ADMIN_IDS 에 더해진다)

재화 이름·양도·출석 보상·툿 보상은 마스토돈(컨텐츠 설정 > 상점)으로 옮겼다. 시트에 남아 있어도 쓰지 않는다.
구매·양도·출석 같은 기록도 마스토돈에 남는다 (예전 기록·출석부 워크시트는 쓰지 않는다).
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, Optional

from .config import Config
from .sheets import Sheets, cell, header_index
from .text import normalize_key

SETTINGS = '설정'

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
