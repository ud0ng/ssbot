"""예약 툿.

예약 툿 시트의 첫 워크시트(또는 '관리')
  날짜 | 시간 | 계정 | 범위 | 문구
  - 날짜: 08/01, 8/1, 2026-08-01    시간: 0:02, 22:01 (한국 시간)
  - 계정: .env 의 ACCOUNT_<이름>
  - 범위: 일반(공개) / 조용한 공개 / 팔로워 / 디엠
시간이 되면 한 번만 올린다. 봇이 꺼져 있던 사이에 지난 예약은 몇 분 전 것까지만 보낸다.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Callable, Optional

from .config import KST
from .sheets import Sheets, cell, header_index
from .state import State
from .text import normalize_key, render

logger = logging.getLogger(__name__)

SHEET = 'reservation'

VISIBILITY = {
    '일반': 'public', '공개': 'public', '퍼블릭': 'public', 'public': 'public',
    '조용한공개': 'unlisted', '미등재': 'unlisted', '타임라인비공개': 'unlisted', 'unlisted': 'unlisted',
    '팔로워': 'private', '비공개': 'private', '팔로워공개': 'private', 'private': 'private',
    '디엠': 'direct', 'dm': 'direct', '다이렉트': 'direct', '멘션': 'direct', 'direct': 'direct',
}

Poster = Callable[[str, str, str], object]


def parse_visibility(text: str) -> str:
    return VISIBILITY.get(normalize_key(text), 'public')


def parse_when(date_text: str, time_text: str, now: datetime) -> Optional[datetime]:
    date_text, time_text = (date_text or '').strip(), (time_text or '').strip()
    full = re.fullmatch(r'(\d{4})[-./](\d{1,2})[-./](\d{1,2})', date_text)
    short = re.fullmatch(r'(\d{1,2})[-./](\d{1,2})', date_text)
    clock = re.fullmatch(r'(오전|오후)?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?', time_text)
    if not clock or not (full or short):
        return None
    meridiem, hour, minute, second = clock.groups()
    hour = int(hour)
    if meridiem == '오후' and hour < 12:
        hour += 12
    elif meridiem == '오전' and hour == 12:
        hour = 0
    year, month, day = (int(full.group(1)), int(full.group(2)), int(full.group(3))) if full \
        else (now.year, int(short.group(1)), int(short.group(2)))
    try:
        return datetime(year, month, day, hour, int(minute), int(second or 0), tzinfo=KST)
    except ValueError:
        return None


class ReservationScheduler:
    def __init__(self, sheets: Sheets, state: State, poster: Poster, catch_up_minutes: int = 10,
                 public_as: str = 'public'):
        self.public_as = public_as
        self.sheets = sheets
        self.state = state
        self.poster = poster
        self.catch_up = timedelta(minutes=catch_up_minutes)

    def _worksheet(self) -> Optional[str]:
        titles = self.sheets.titles(SHEET)
        if '관리' in titles:
            return '관리'
        return titles[0] if titles else None

    def due(self, now: datetime) -> list:
        worksheet = self._worksheet()
        if worksheet is None:
            return []
        rows = self.sheets.rows(SHEET, worksheet)
        try:
            header_row, columns = header_index(rows, '날짜', '시간', '문구')
        except LookupError:
            logger.warning('예약 툿 시트에서 날짜 | 시간 | 문구 헤더를 찾을 수 없습니다.')
            return []
        result = []
        for row in rows[header_row + 1:]:
            text = cell(row, columns['문구']).replace('​', '')
            when = parse_when(cell(row, columns['날짜']), cell(row, columns['시간']), now)
            if not text or when is None:
                continue
            if now - self.catch_up <= when <= now:
                account = (cell(row, columns.get('계정')) or 'NOTICE').upper()
                visibility = parse_visibility(cell(row, columns.get('범위')))
                if visibility == 'public':
                    visibility = self.public_as
                key = hashlib.sha1(f'{when.isoformat()}|{account}|{visibility}|{text}'.encode()).hexdigest()
                result.append((key, when, account, visibility, text))
        return result

    def tick(self, now: datetime) -> int:
        if not self.sheets.has(SHEET):
            return 0
        sent = self.state.section('reservations')
        posted = 0
        for key, when, account, visibility, text in self.due(now):
            if key in sent:
                continue
            # 먼저 기록해서, 송출이 느리거나 실패해도 두 번 올라가지 않게 한다.
            self.state.set('reservations', key, when.isoformat())
            try:
                self.poster(account, render(text), visibility)
                posted += 1
                logger.info('예약 툿 송출: %s %s (%s)', when.strftime('%m/%d %H:%M'), account, visibility)
            except Exception:
                logger.exception('예약 툿 송출 실패: %s %s', when.isoformat(), account)

        # 사흘 지난 기록은 정리
        cutoff = (now - timedelta(days=3)).isoformat()
        stale = {k: v for k, v in self.state.section('reservations').items() if v >= cutoff}
        if len(stale) != len(self.state.section('reservations')):
            self.state.replace_section('reservations', stale)
        return posted
