"""조사 자동봇.

조사 시트
  진입 워크시트       장소명 | 진입 시 문구 | 현재 진입 가능 (가능/불가능)
                      같은 장소명을 여러 줄 적으면 그중 하나가 랜덤으로 나온다.
  장소별 워크시트     조사 포인트 | 조사 시 문구 | 획득 아이템 | 변동 스탯
                      워크시트 이름 = 장소명. 같은 포인트를 여러 줄 적으면 랜덤.
                      획득 아이템: "기념주화: 3, 쪽지: 1"   변동 스탯: "체력: -3, 행운: +1d3"

명령어
  [장소 목록]  [진입/장소명]  [조사/포인트명]
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Callable, Optional

from .config import Config
from .game import Ledger, find_item_key, parse_items, parse_stat_changes, _to_int
from .sheets import Sheets, WorksheetNotFound, cell, header_index
from .state import State
from .text import DiceError, amount, josa, normalize_key, render

SHEET = 'investigation'
ENTRY = '진입'
AVAILABLE = {'가능', 'o', 'O', 'true', 'TRUE', '✓', '✔'}
UNAVAILABLE = {'불가능', 'x', 'X', 'false', 'FALSE'}


class Investigation:
    def __init__(self, config: Config, sheets: Sheets, state: State, ledger: Ledger,
                 rng: random.Random, clock: Callable[[], datetime]):
        self.config = config
        self.sheets = sheets
        self.state = state
        self.ledger = ledger
        self.rng = rng
        self.clock = clock

    def handle(self, user: str, key: str, args: list, caster: str) -> Optional[str]:
        if key not in ('장소목록', '진입', '조사') or not self.sheets.has(SHEET):
            return None
        if key == '장소목록':
            return self.place_list()
        if not args or not args[0]:
            return f"형식: [{'진입/장소명' if key == '진입' else '조사/포인트명'}]"
        if key == '진입':
            return self.enter(user, args[0], caster)
        return self.investigate(user, args[0], caster)

    # ------------------------------------------------------------ 진입

    def _entries(self):
        rows = self.sheets.rows_or_empty(SHEET, ENTRY)
        if not rows:
            return []
        header_row, columns = header_index(rows, '장소명')
        text_col = next((col for name, col in columns.items() if '문구' in name), None)
        open_col = next((col for name, col in columns.items() if '진입 가능' in name or name == '진입가능'), None)
        entries = []
        for row in rows[header_row + 1:]:
            place, text = cell(row, columns['장소명']), cell(row, text_col)
            status = cell(row, open_col)
            if not place or not text:
                continue
            if status and status not in AVAILABLE and status not in UNAVAILABLE:
                continue  # 설명 행
            entries.append((place, text, status not in UNAVAILABLE))
        return entries

    def place_list(self) -> str:
        names = []
        for place, _, is_open in self._entries():
            if is_open and place not in names:
                names.append(place)
        if not names:
            return '지금은 진입할 수 있는 장소가 없습니다.'
        return '\n'.join(['현재 진입할 수 있는 장소는 다음과 같습니다.', ''] + [f'- {name}' for name in names]
                         + ['', '[진입/장소명]으로 조사를 시작할 수 있습니다.'])

    def enter(self, user: str, place: str, caster: str) -> str:
        target = normalize_key(place)
        candidates = [entry for entry in self._entries() if normalize_key(entry[0]) == target]
        if not candidates:
            return f"'{place}'{josa(place, '은', '는')} 없는 장소입니다. [장소 목록]을 확인해 주세요."
        open_candidates = [entry for entry in candidates if entry[2]]
        if not open_candidates:
            return f'{candidates[0][0]}에는 지금 진입할 수 없습니다.'
        name, text, _ = self.rng.choice(open_candidates)
        self.state.set('places', user.lower(), name)
        return render(text, caster, self.rng)

    # ------------------------------------------------------------ 조사

    def investigate(self, user: str, point: str, caster: str) -> str:
        place = self.state.get('places', user.lower())
        if not place:
            return '먼저 [진입/장소명]으로 장소에 들어가 주세요.'

        try:
            rows = self.sheets.rows(SHEET, place)
        except WorksheetNotFound:
            return f"'{place}' 장소의 조사 시트가 없습니다. 운영진에게 문의해 주세요."
        header_row, columns = header_index(rows, '조사 포인트')
        text_col = next((col for name, col in columns.items() if '문구' in name), None)
        item_col = next((col for name, col in columns.items() if '아이템' in name), None)
        stat_col = next((col for name, col in columns.items() if '스탯' in name), None)

        target = normalize_key(point)
        candidates = [row for row in rows[header_row + 1:]
                      if normalize_key(cell(row, columns['조사 포인트'])) == target and cell(row, text_col)]
        if not candidates:
            return f"{place}에는 '{point}'{josa(point, '이라는', '라는')} 조사 포인트가 없습니다."

        limit = self.config.investigation_daily_limit
        today = f'{self.clock():%Y-%m-%d}'
        if limit > 0 and self.state.daily('investigations', today, user.lower()) >= limit:
            return f'오늘의 조사 횟수({limit}회)를 모두 사용했습니다. 내일 다시 시도해 주세요.'

        row = self.rng.choice(candidates)
        point_name = cell(row, columns['조사 포인트'])
        lines = [f'[{point_name}]', '', render(cell(row, text_col), caster, self.rng)]
        effects = self._apply(user, cell(row, item_col), cell(row, stat_col))
        if effects:
            lines += [''] + effects

        if limit > 0:
            used = self.state.increment_daily('investigations', today, user.lower())
            lines += ['', f'(오늘 조사 {used}/{limit}회)']
        return '\n'.join(lines)

    def _apply(self, user: str, items_text: str, stats_text: str) -> list:
        items = parse_items(items_text)
        stats = parse_stat_changes(stats_text)
        if not items and not stats:
            return []

        with self.sheets.write_lock:
            character = self.ledger.find(user, fresh=True) if self.ledger.available else None
            effects = []
            changed_stats = []
            for name, count in items.items():
                effects.append(f"➭ '{name}' {count}개 획득")
                if character is not None:
                    existing = find_item_key(character.items, name) or name
                    character.items[existing] = character.items.get(existing, 0) + count
            for stat, expression in stats:
                try:
                    delta = amount(expression, self.rng)
                except DiceError:
                    continue
                effects.append(f'➭ {stat} {delta:+d}')
                if character is not None:
                    stat_key = character.stat_key(stat)
                    if stat_key is not None:
                        character.stats[stat_key] = str(_to_int(character.stats[stat_key]) + delta)
                        changed_stats.append(stat_key)
            if character is not None:
                self.ledger.save(character, items=bool(items), stats=changed_stats)
        return effects
