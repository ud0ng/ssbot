"""기본 자동봇 시트의 캐릭터·상점 데이터.

관리 워크시트
  이름 | 아이디 | 소지금 | 소지품 | (스탯 열들: 체력, 정신력 ...)
  - 헤더 이름으로 열을 찾는다. 빈 열이 끼어 있어도 된다.
  - 소지품: "사과: 2, 바나나: 1"
  - 소지품 오른쪽에 있는 나머지 열은 모두 스탯으로 본다.

명단 워크시트 (상점이 없는 기본 타입에서 이름을 찾을 때)
  이름 | 아이디

상점 워크시트
  아이템명 | 가격 | 설명 | 스탯 | 수치
  - 가격이 숫자가 아니면(비매품, 빈칸) 살 수 없다.
  - 스탯이 '사용 불가'면 [사용]할 수 없다. 수치는 정수나 주사위(1d10, -(1d6+3)).
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .sheets import Sheets, cell, header_index
from .text import normalize_key

MAIN = 'main'
LEDGER = '관리'
ROSTER = '명단'
SHOP = '상점'

ALL_TARGETS = {'전원', '모두'}
_KNOWN_COLUMNS = {'이름', '아이디', '소지금', '소지품'}


# ---------------------------------------------------------------- 소지품

def parse_items(text: str) -> 'OrderedDict[str, int]':
    """'사과: 2, 바나나: 1' → {'사과': 2, '바나나': 1}. 수량이 없으면 1."""
    items: 'OrderedDict[str, int]' = OrderedDict()
    for part in re.split(r'[,，]', text or ''):
        part = part.strip()
        if not part:
            continue
        match = re.match(r'^(.*?)\s*[:：]\s*([+-]?\d+)\s*(?:개)?$', part)
        name, count = (match.group(1).strip(), int(match.group(2))) if match else (part, 1)
        if name:
            items[name] = items.get(name, 0) + count
    return items


def format_items(items: Dict[str, int]) -> str:
    return ', '.join(f'{name}: {count}' for name, count in items.items() if count > 0)


def find_item_key(items: Dict[str, int], name: str) -> Optional[str]:
    target = normalize_key(name)
    for key in items:
        if normalize_key(key) == target:
            return key
    return None


def parse_stat_changes(text: str) -> List[tuple]:
    """'체력: -3, 행운: +1d3' → [('체력', '-3'), ('행운', '+1d3')]"""
    changes = []
    for part in re.split(r'[,，]', text or ''):
        if ':' in part or '：' in part:
            name, value = re.split(r'[:：]', part, maxsplit=1)
            if name.strip() and value.strip():
                changes.append((name.strip(), value.strip()))
    return changes


# ---------------------------------------------------------------- 캐릭터

@dataclass
class Character:
    row: int                      # 시트 행 번호 (1-based)
    name: str
    acct: str
    money: int = 0
    items: 'OrderedDict[str, int]' = field(default_factory=OrderedDict)
    stats: 'OrderedDict[str, str]' = field(default_factory=OrderedDict)

    def stat_key(self, name: str) -> Optional[str]:
        target = normalize_key(name)
        for key in self.stats:
            if normalize_key(key) == target:
                return key
        return None


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(float((value or '').replace(',', '').strip()))
    except ValueError:
        return default


def acct_matches(acct: str, user: str) -> bool:
    """시트의 아이디와 마스토돈 acct 비교 (@, 도메인 무시)."""
    def local(value: str) -> str:
        return (value or '').strip().lstrip('@').split('@')[0].lower()
    return bool(acct) and local(acct) == local(user)


class Ledger:
    """관리 워크시트."""

    def __init__(self, sheets: Sheets):
        self.sheets = sheets

    @property
    def available(self) -> bool:
        return bool(self.sheets.rows_or_empty(MAIN, LEDGER))

    def _layout(self, rows):
        header_row, columns = header_index(rows, '이름', '아이디')
        stat_columns = OrderedDict()
        items_col = columns.get('소지품')
        start = (items_col if items_col is not None else max(columns.values())) + 1
        for name, col in sorted(columns.items(), key=lambda pair: pair[1]):
            if name not in _KNOWN_COLUMNS and col >= start:
                stat_columns[name] = col
        return header_row, columns, stat_columns

    def characters(self, fresh: bool = False) -> List[Character]:
        rows = self.sheets.rows_or_empty(MAIN, LEDGER, fresh)
        if not rows:
            return []
        header_row, columns, stat_columns = self._layout(rows)
        result = []
        for index, row in enumerate(rows[header_row + 1:], start=header_row + 2):
            name, acct = cell(row, columns.get('이름')), cell(row, columns.get('아이디'))
            if not name and not acct:
                continue
            result.append(Character(
                row=index,
                name=name,
                acct=acct,
                money=_to_int(cell(row, columns.get('소지금'))),
                items=parse_items(cell(row, columns.get('소지품'))),
                stats=OrderedDict((stat, cell(row, col)) for stat, col in stat_columns.items()),
            ))
        return result

    def find(self, user: str, fresh: bool = False) -> Optional[Character]:
        """마스토돈 아이디(acct)로 찾는다."""
        for character in self.characters(fresh):
            if acct_matches(character.acct, user):
                return character
        return None

    def find_by_name(self, name: str, fresh: bool = False) -> Optional[Character]:
        target = normalize_key(name)
        for character in self.characters(fresh):
            if normalize_key(character.name) == target or acct_matches(character.acct, name):
                return character
        return None

    def targets(self, names: str, fresh: bool = True) -> tuple[List[Character], List[str]]:
        """'겐지, 디바' 또는 '전원' → (찾은 캐릭터, 못 찾은 이름)"""
        characters = self.characters(fresh)
        if normalize_key(names) in ALL_TARGETS:
            return characters, []
        found, missing = [], []
        for name in [part.strip() for part in re.split(r'[,，]', names or '') if part.strip()]:
            target = normalize_key(name)
            match = next((c for c in characters if normalize_key(c.name) == target or acct_matches(c.acct, name)), None)
            (found.append(match) if match else missing.append(name))
        return found, missing

    def save(self, character: Character, money: bool = False, items: bool = False, stats: Iterable[str] = ()) -> None:
        rows = self.sheets.rows(MAIN, LEDGER)
        _, columns, stat_columns = self._layout(rows)
        cells = {}
        if money and '소지금' in columns:
            cells[(character.row, columns['소지금'] + 1)] = str(character.money)
        if items and '소지품' in columns:
            cells[(character.row, columns['소지품'] + 1)] = format_items(character.items)
        for stat in stats:
            if stat in stat_columns:
                cells[(character.row, stat_columns[stat] + 1)] = character.stats[stat]
        self.sheets.update(MAIN, LEDGER, cells)


def display_name(sheets: Sheets, user: str, fallback: str = '') -> str:
    """{시전자} 에 쓸 이름: 명단 → 관리 → 마스토돈 표시 이름."""
    for worksheet in (ROSTER, LEDGER):
        rows = sheets.rows_or_empty(MAIN, worksheet)
        if not rows:
            continue
        try:
            header_row, columns = header_index(rows, '이름', '아이디')
        except LookupError:
            continue
        for row in rows[header_row + 1:]:
            if acct_matches(cell(row, columns['아이디']), user) and cell(row, columns['이름']):
                return cell(row, columns['이름'])
    return fallback or user


# ---------------------------------------------------------------- 상점

@dataclass
class ShopItem:
    name: str
    price: Optional[int]
    description: str
    stat: str
    value: str

    @property
    def for_sale(self) -> bool:
        return self.price is not None

    @property
    def usable(self) -> bool:
        return self.stat not in ('사용 불가', '사용불가')


def shop_items(sheets: Sheets) -> List[ShopItem]:
    rows = sheets.rows_or_empty(MAIN, SHOP)
    if not rows:
        return []
    header_row, columns = header_index(rows, '아이템명')
    items = []
    for row in rows[header_row + 1:]:
        name = cell(row, columns.get('아이템명'))
        if not name:
            continue
        price_text = cell(row, columns.get('가격')).replace(',', '')
        items.append(ShopItem(
            name=name,
            price=int(price_text) if re.fullmatch(r'\d+', price_text) else None,
            description=cell(row, columns.get('설명')),
            stat=cell(row, columns.get('스탯')),
            value=cell(row, columns.get('수치')),
        ))
    return items


def find_shop_item(sheets: Sheets, name: str) -> Optional[ShopItem]:
    target = normalize_key(name)
    return next((item for item in shop_items(sheets) if normalize_key(item.name) == target), None)
