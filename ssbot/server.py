"""마스토돈 서버의 캐릭터·재화·상점 (예전 기본 시트의 명단·관리·상점·출석부·기록 워크시트를 대신한다).

마스토돈 관리 화면
  캐릭터 관리      캐릭터 이름·소속·스탯·돈·소지품 (회원 목록에서 자동으로 만들어진다)
  컨텐츠 설정 > 상점  상점·아이템, 재화 규칙(양도·출석 보상·툿 보상), 도움말, 최근 재화 기록
  컨텐츠 설정       재화 이름, '자동봇 계정 아이디' (이 봇 계정이 들어 있어야 API 를 쓸 수 있다)

API (봇 계정 토큰, MASTODON_ACCESS_TOKEN)
  GET  /api/v1/bot/config              재화 이름·양도·출석·툿 보상·도움말
  GET  /api/v1/bot/characters          캐릭터 목록
  GET  /api/v1/bot/shops               상점과 아이템
  POST /api/v1/bot/characters/apply    돈·아이템·스탯 바꾸기 (여러 명을 한꺼번에, 모두 되거나 하나도 안 됨)
       모자라면 422 { code: insufficient_money / insufficient_items / already_done / unknown_stat / not_found }
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import requests

from .text import normalize_key

logger = logging.getLogger(__name__)

ALL_TARGETS = {'전원', '모두'}
CACHE_SECONDS = 30


# ---------------------------------------------------------------- 소지품 문자열

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


def acct_matches(acct: str, user: str) -> bool:
    """마스토돈 acct 비교 (@, 도메인 무시)."""
    def local(value: str) -> str:
        return (value or '').strip().lstrip('@').split('@')[0].lower()
    return bool(acct) and local(acct) == local(user)


# ---------------------------------------------------------------- 데이터

@dataclass
class Character:
    acct: str
    name: str
    money: int = 0
    items: 'OrderedDict[str, int]' = field(default_factory=OrderedDict)
    stats: 'OrderedDict[str, int]' = field(default_factory=OrderedDict)      # 바꿀 수 있는 스탯
    statuses: 'OrderedDict[str, object]' = field(default_factory=OrderedDict)  # 스탯으로 계산한 상태 수치 (읽기 전용)

    def stat_key(self, name: str) -> Optional[str]:
        """바꿀 수 있는 스탯 이름 (상태 수치는 None)."""
        target = normalize_key(name)
        return next((key for key in self.stats if normalize_key(key) == target), None)

    @classmethod
    def from_json(cls, data: dict) -> 'Character':
        stats, statuses = OrderedDict(), OrderedDict()
        for stat in data.get('stats') or []:
            (stats if stat.get('type') == 'stat' else statuses)[stat['name']] = stat.get('value')
        return cls(
            acct=data['acct'],
            name=data.get('name') or data['acct'],
            money=int(data.get('money') or 0),
            items=OrderedDict((item['name'], int(item.get('count') or 0)) for item in data.get('items') or []),
            stats=stats,
            statuses=statuses,
        )


@dataclass
class ShopItem:
    name: str
    price: Optional[int]
    description: str = ''
    stat: str = ''
    value: str = ''
    usable: bool = True
    permanent: bool = False    # [영구] 아이템: [사용] 해도 사라지지 않음
    use_target: str = 'stat'   # stat(본인 스탯) / character(태그한 캐릭터) / special(신고·기숙사 점수·운영진 DM·호울러)
    shop: str = ''

    @property
    def for_sale(self) -> bool:
        return self.price is not None


@dataclass
class HelpTopic:
    """분류별 도움말 하나 (마스토돈 컨텐츠 설정 > 도움말 > 상점봇). key: shop / admin"""
    key: str
    command: str
    description: str = ''
    admin: bool = False


@dataclass
class ServerSettings:
    currency: str = '코인'
    transfer_enabled: bool = True
    shop_open: bool = True              # 상점 해금 (끄면 [상점]·[구매]를 받지 않는다)
    attendance_reward: int = 0
    toot_reward_every: int = 0
    toot_reward_amount: int = 1
    help: List[str] = field(default_factory=list)          # [상점 도움말]
    admin_help: List[str] = field(default_factory=list)    # [운영진 도움말]
    basic_help: List[str] = field(default_factory=list)    # [도움말] 맨 위 (비면 기본 줄)
    help_command: str = ''              # 컨텐츠 설정 > 도움말의 [도움말] 이름 (없으면 commands['help'])
    help_topics: List[HelpTopic] = field(default_factory=list)
    # 컨텐츠 설정 > 상점에서 바꾼 명령어 이름 ({'buy': '사기', 'stat_change': '변경', ...}) 과 운영진 계정
    commands: Dict[str, str] = field(default_factory=dict)
    admins: List[str] = field(default_factory=list)


class LedgerError(Exception):
    """서버가 거절함 (모자람·오늘 이미 함·없는 스탯 등). message 는 사용자에게 보여 줄 문구."""

    def __init__(self, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class ServerUnavailable(Exception):
    """서버에 연결할 수 없음 (주소·토큰·권한)."""


# ---------------------------------------------------------------- HTTP

class Api:
    """마스토돈 /api/v1/bot 호출."""

    def __init__(self, base_url: str, token: str, timeout: int = 15):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers['Authorization'] = f'Bearer {token}'
        self.timeout = timeout

    def _call(self, method: str, path: str, **kwargs):
        try:
            response = self.session.request(method, f'{self.base_url}/api/v1/bot/{path}', timeout=self.timeout, **kwargs)
        except requests.RequestException as error:
            raise ServerUnavailable(f'마스토돈 서버에 연결할 수 없습니다: {error}') from error
        if response.status_code == 422:
            body = response.json()
            raise LedgerError(body.get('code', 'error'), body.get('error', '처리할 수 없습니다.'), body.get('details'))
        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            raise ServerUnavailable('봇 계정이 캐릭터·재화 API 를 쓸 수 없습니다. 마스토돈 컨텐츠 설정의 '
                                    "'자동봇 계정 아이디'에 이 봇 계정을 넣어 주세요.")
        response.raise_for_status()
        return response.json()

    def get(self, path: str, params: Optional[dict] = None):
        return self._call('GET', path, params=params)

    def post(self, path: str, json: dict):
        return self._call('POST', path, json=json)


# ---------------------------------------------------------------- 캐릭터·상점

class ServerLedger:
    """캐릭터·상점·재화 설정. 읽은 것은 잠깐(CACHE_SECONDS) 기억하고, 바꿀 때는 서버에 바로 보낸다."""

    def __init__(self, api, cache_seconds: int = CACHE_SECONDS):
        self.api = api
        self.cache_seconds = cache_seconds
        self._cache: Dict[str, tuple] = {}
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.api is not None

    def _cached(self, key: str, loader, fresh: bool = False):
        with self._lock:
            hit = self._cache.get(key)
            if hit and not fresh and time.monotonic() - hit[0] < self.cache_seconds:
                return hit[1]
        value = loader()
        with self._lock:
            self._cache[key] = (time.monotonic(), value)
        return value

    def invalidate(self) -> int:
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    # ---- 설정

    def settings(self) -> ServerSettings:
        def load():
            data = self.api.get('config') or {}
            return ServerSettings(
                currency=data.get('currency') or '코인',
                transfer_enabled=bool(data.get('transfer_enabled', True)),
                shop_open=bool(data.get('shop_open', True)),
                attendance_reward=int(data.get('attendance_reward') or 0),
                toot_reward_every=int(data.get('toot_reward_every') or 0),
                toot_reward_amount=int(data.get('toot_reward_amount') or 0),
                help=list(data.get('help') or []),
                admin_help=list(data.get('admin_help') or []),
                commands={str(key): str(name).strip() for key, name in (data.get('commands') or {}).items() if str(name).strip()},
                admins=[str(acct).strip().lstrip('@') for acct in data.get('admins') or [] if str(acct).strip()],
                basic_help=list(data.get('basic_help') or []),
                help_command=str(data.get('help_command') or '').strip(),
                help_topics=[HelpTopic(key=str(topic.get('key') or ''), command=str(topic.get('command') or ''),
                                       description=str(topic.get('description') or ''), admin=bool(topic.get('admin')))
                             for topic in data.get('help_topics') or [] if topic.get('command')],
            )
        return self._cached('config', load)

    # ---- 캐릭터

    def characters(self, fresh: bool = False) -> List[Character]:
        return self._cached('characters', lambda: [Character.from_json(row) for row in self.api.get('characters') or []], fresh)

    def find(self, user: str, fresh: bool = False) -> Optional[Character]:
        """마스토돈 아이디(acct)로 찾는다."""
        return next((character for character in self.characters(fresh) if acct_matches(character.acct, user)), None)

    def find_by_name(self, name: str, fresh: bool = False) -> Optional[Character]:
        target = normalize_key(name)
        return next((c for c in self.characters(fresh) if normalize_key(c.name) == target or acct_matches(c.acct, name)), None)

    def search(self, name: str, fresh: bool = False) -> List[Character]:
        """캐릭터 이름 또는 계정 아이디로 찾는다. 아이디·이름이 정확히 같은 캐릭터가 있으면 그쪽만, 없으면 이름이 그 말로 시작하는
        캐릭터, 그다음 이름에 그 말이 들어 있는 캐릭터 (예: '에이비 시디' 는 '에이비' 만 써도 찾는다). 여럿이면 여럿 다 돌려준다."""
        characters = self.characters(fresh)
        target = normalize_key(name.lstrip('@＠'))
        if not target:
            return []
        for matches in (
            [c for c in characters if acct_matches(c.acct, name.lstrip('＠'))],
            [c for c in characters if normalize_key(c.name) == target],
            [c for c in characters if normalize_key(c.name).startswith(target)],
            [c for c in characters if target in normalize_key(c.name)],
        ):
            if matches:
                return matches
        return []

    def targets(self, names: str, fresh: bool = True) -> tuple[List[Character], List[str]]:
        """'겐지, 디바' 또는 '전원' → (찾은 캐릭터, 못 찾은 이름)"""
        characters = self.characters(fresh)
        if normalize_key(names) in ALL_TARGETS:
            return characters, []
        found, missing = [], []
        for name in [part.strip() for part in re.split(r'[,，]', names or '') if part.strip()]:
            target = normalize_key(name)
            matches = self.search(name)
            (found.append(matches[0]) if len(matches) == 1 else missing.append(name if not matches else f"{name}(여럿: {', '.join(c.name for c in matches)})"))
        return found, missing

    def display_name(self, user: str, fallback: str = '') -> str:
        """{시전자} 에 쓸 이름: 캐릭터 이름 → 마스토돈 표시 이름."""
        try:
            character = self.find(user)
        except (ServerUnavailable, LedgerError):
            character = None
        return character.name if character and character.name else (fallback or user)

    def apply(self, changes: List[dict], kind: str, detail: str = '', once_per_day: bool = False) -> Dict[str, Character]:
        """changes: [{'acct': , 'money': , 'items': {이름: 개수}, 'stats': {이름: 값}}] → 바뀐 캐릭터 (acct 소문자 → Character)."""
        payload = {
            'kind': kind,
            'detail': detail,
            'once_per_day': once_per_day,
            'changes': [{key: value for key, value in change.items() if value not in (None, {}, 0) or key == 'acct'} for change in changes],
        }
        result = self.api.post('characters/apply', payload) or {}
        with self._lock:
            self._cache.pop('characters', None)
        updated = [Character.from_json(row) for row in result.get('characters') or []]
        return {character.acct.lower(): character for character in updated}

    def use_item(self, acct: str, name: str, args: Optional[List[str]] = None) -> Tuple[str, List[str]]:
        """아이템 [사용]: 효과(스탯·재화·누적 수치·효과 등)는 웹 인벤토리와 같은 규칙으로 서버가 처리한다.
        args 는 [사용/아이템/…] 뒤에 붙은 말들 (@상대·캐릭터 이름·소속 이름). (안내 문장, 답글에 태그할 상대 아이디들) 을 돌려준다."""
        result = self.api.post('items/use', {'acct': acct, 'name': name, 'args': list(args or [])}) or {}
        with self._lock:
            self._cache.pop('characters', None)
        return result.get('message') or '', [str(acct) for acct in result.get('notify') or []]

    # ---- 상점

    def shop_items(self) -> List[ShopItem]:
        def load():
            items = []
            for shop in self.api.get('shops') or []:
                for item in shop.get('items') or []:
                    items.append(ShopItem(name=item['name'], price=item.get('price'), description=item.get('description') or '',
                                          stat=item.get('stat') or '', value=item.get('value') or '',
                                          usable=bool(item.get('usable', True)), permanent=bool(item.get('permanent', False)), use_target=item.get('use_target') or 'stat',
                                          shop=shop.get('name') or ''))
            return items
        return self._cached('shops', load)

    def find_shop_item(self, name: str) -> Optional[ShopItem]:
        target = normalize_key(name)
        return next((item for item in self.shop_items() if normalize_key(item.name) == target), None)
