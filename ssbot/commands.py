"""명령어 처리. 툿의 [대괄호] 안 내용을 받아 답장 문구를 만든다.

처리 순서
  1. 운영진 명령어      [캐시 리셋] [소지금 추가/차감] [아이템 추가/차감] [OO 변경]
  2. 기본               [도움말] [NdM] [랜덤/A, B] [운세]
  3. 상점               [가방] [상점] [구매/템] [설명/템] [버리기/템]
  4. 스탯               [상태 확인] [사용/템]
  5. 옵션               [양도/..] [출석] [랜덤박스/박스]
  6. 조사               [장소 목록] [진입/장소] [조사/포인트]
  7. 키워드 시트        운영진이 적은 키워드 답변

캐릭터·돈·소지품·스탯·상점·재화 규칙은 마스토돈 서버에 있다 (server.py).
시트에는 도움말·운세·키워드·랜덤박스·설정(키워드 이미지·조사 횟수·랜덤박스 명령어·운영진)과 예약 툿·스토리·조사가 남는다.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable, List, Optional

from .config import Config, now_kst
from .investigation import Investigation
from .options import effective
from .server import Character, HelpTopic, LedgerError, ServerLedger, find_item_key, parse_items
from .sheets import Sheets, cell, header_index
from .state import State
from .text import DiceError, amount, is_dice, josa, normalize_key, render, roll, split_options

HELP = '도움말'
KEYWORDS = '키워드'
FORTUNE = '운세'
RANDOMBOX = '랜덤박스'

DEFAULT_HELP = [
    '[도움말] 도움말을 보여줍니다.',
    '[NdM] M면체 주사위를 N개 굴립니다.',
    '[랜덤/A, B, C...] 여러 옵션 중 1개를 골라 출력합니다.',
    '[운세] 오늘의 운세를 확인합니다.',
]

# 마스토돈 컨텐츠 설정 > 상점에서 이름을 바꿀 수 있는 명령어: 서버 설정 키 → 자동봇이 처리하는 원래 이름(normalize_key)
COMMAND_KEYS = {
    'bag': '가방', 'shop': '상점', 'buy': '구매', 'describe': '설명', 'discard': '버리기', 'use': '사용',
    'status': '상태확인', 'transfer': '양도', 'attendance': '출석', 'help': '도움말',
    'money_add': '소지금추가', 'money_sub': '소지금차감', 'item_add': '아이템추가', 'item_sub': '아이템차감',
}
STAT_CHANGE = '변경'   # [힘 변경/+3/캐릭터명] 의 뒷말 (서버 설정 stat_change)

# 분류별 도움말 기본값 (마스토돈이 help_topics 를 주지 않을 때). 명령어·설명은 마스토돈 컨텐츠 설정 > 도움말에서 바꾼다
DEFAULT_HELP_TOPICS = [
    HelpTopic('shop', '상점 도움말', '재화·아이템·상점 명령어를 보여줍니다.'),
    HelpTopic('admin', '운영진 도움말', '운영진 명령어를 보여줍니다.', admin=True),
]

@dataclass
class Request:
    user: str                     # 마스토돈 acct
    command: str                  # [ ] 안의 내용
    display_name: str = ''        # 마스토돈 표시 이름 ({시전자} 폴백)
    is_admin: bool = False


@dataclass
class Reply:
    text: str
    media_urls: List[str] = field(default_factory=list)


def first_command(text: str) -> Optional[str]:
    """툿 본문에서 첫 번째 [대괄호] 내용을 꺼낸다."""
    match = re.search(r'\[([^\[\]]+)\]', text or '')
    return match.group(1).strip() if match else None


def with_unit(count: int) -> str:
    return f'{count}개'


def describe_items(items) -> str:
    listed = [f'{name} {with_unit(count)}' for name, count in items.items() if count > 0]
    return ', '.join(listed) if listed else '없음'


class Processor:
    def __init__(self, config: Config, sheets: Sheets, state: State, ledger: ServerLedger,
                 rng: Optional[random.Random] = None, clock: Callable[[], datetime] = now_kst):
        self.base_config = config
        self.config = config
        self.sheets = sheets
        self.state = state
        self.ledger = ledger
        self.rng = rng or random.Random()
        self.clock = clock
        self.investigation = Investigation(config, sheets, state, ledger, self.rng, clock)
        self.aliases = {}          # 바꾼 이름(normalize_key) → 원래 이름
        self.renamed = set()       # 이름을 바꿔서 더는 받지 않는 원래 이름
        self.names = {}            # 원래 이름 → 지금 쓰는 이름 (안내 문구용)
        self.stat_change = STAT_CHANGE

    def effective_config(self) -> Config:
        """시트 설정 탭 + 마스토돈 재화 규칙·명령어 이름·운영진을 반영한 설정 (바꾸면 다음 명령어부터 적용)."""
        config = effective(self.base_config, self.sheets)
        if self.ledger.available:
            server = self.ledger.settings()
            config = replace(config, currency=server.currency, transfer_enabled=server.transfer_enabled,
                             attendance_reward=server.attendance_reward, toot_reward_every=server.toot_reward_every,
                             toot_reward_amount=server.toot_reward_amount,
                             admins=list(dict.fromkeys(list(config.admins) + list(server.admins))))
            # 도움말 이름은 컨텐츠 설정 > 도움말 (help_command), 나머지는 상점 탭의 명령어 이름
            self._load_commands({**server.commands, **({'help': server.help_command} if server.help_command else {})})
        return config

    def _load_commands(self, commands) -> None:
        """서버가 준 명령어 이름: 바꾼 이름은 원래 이름으로 처리하고, 바뀐 원래 이름은 받지 않는다."""
        self.aliases, self.renamed, self.names = {}, set(), {}
        for setting, original in COMMAND_KEYS.items():
            name = (commands or {}).get(setting) or original
            self.names[original] = name
            custom = normalize_key(name)
            if custom and custom != original:
                self.aliases[custom] = original
                self.renamed.add(original)
        self.stat_change = (commands or {}).get('stat_change') or STAT_CHANGE

    def _command_key(self, key: str) -> str:
        if key in self.aliases:
            return self.aliases[key]
        if key in self.renamed:
            return ''   # 예전 이름: 어느 처리기에도 걸리지 않게
        return key

    def _name(self, original: str) -> str:
        return self.names.get(original, original)

    # ------------------------------------------------------------ 입구

    def handle(self, request: Request) -> Optional[Reply]:
        parts = [part.strip() for part in request.command.split('/')]
        head = parts[0]
        key = normalize_key(head)
        args = parts[1:]
        self.config = self.effective_config()
        self.investigation.config = self.config
        key = self._command_key(key)
        if self.config.is_admin(request.user):
            request.is_admin = True
        fallback = request.display_name or request.user
        caster = self.ledger.display_name(request.user, fallback) if self.ledger.available else fallback

        for handler in (self._admin, self._basic, self._shop, self._stats, self._options):
            try:
                reply = handler(request, key, head, args, caster)
            except LedgerError as error:
                reply = str(error)
            if reply is not None:
                return reply if isinstance(reply, Reply) else Reply(reply)

        reply = self.investigation.handle(request.user, key, args, caster)
        if reply is not None:
            return Reply(reply)

        reply = self._keyword(request.command, head, caster)
        if reply is not None:
            return reply

        help_name = self._name(HELP)
        return Reply(f'[{head}]{josa(head, "은", "는")} 없는 명령어입니다. [{help_name}]{josa(help_name, "을", "를")} 확인해 주세요.')

    # ------------------------------------------------------------ 공통

    def _character(self, user: str) -> Optional[Character]:
        return self.ledger.find(user, fresh=True)

    def _not_registered(self) -> str:
        return '캐릭터 관리에 등록된 캐릭터가 아닙니다. 운영진에게 문의해 주세요.'

    def _money(self, value: int) -> str:
        return f'{value}{self.config.currency}'

    def _apply_one(self, character: Character, kind: str, detail: str = '', money: int = 0, items=None, stats=None,
                   once_per_day: bool = False) -> Character:
        result = self.ledger.apply([{'acct': character.acct, 'money': money, 'items': dict(items or {}), 'stats': dict(stats or {})}],
                                   kind, detail, once_per_day)
        return result.get(character.acct.lower(), character)

    # ------------------------------------------------------------ 운영진

    def _admin(self, request, key, head, args, caster):
        admin_keys = {'캐시리셋', '시트업데이트', '소지금추가', '소지금차감', '아이템추가', '아이템차감'}
        suffix = normalize_key(self.stat_change)
        is_stat_change = bool(suffix) and key.endswith(suffix) and len(key) > len(suffix)
        if key not in admin_keys and not is_stat_change:
            return None
        if not request.is_admin:
            return '운영진만 사용할 수 있는 명령어입니다.'

        if key in ('캐시리셋', '시트업데이트'):
            count = self.sheets.invalidate() + self.ledger.invalidate()
            return f'캐시를 비웠습니다. ({count}개) 다음 명령어부터 시트·서버 내용이 새로 반영됩니다.'

        if len(args) < 2:
            return f'형식: [{head}/값/캐릭터명] (캐릭터명은 쉼표로 여러 명, 또는 전원)'

        targets, missing = self.ledger.targets(args[1])
        if not targets:
            return f"캐릭터를 찾을 수 없습니다: {', '.join(missing) or args[1]}"

        lines = []
        detail = f'{head}/{args[0]} (@{request.user})'
        try:
            if key in ('소지금추가', '소지금차감'):
                sign = 1 if key == '소지금추가' else -1
                for character in targets:
                    delta = sign * abs(amount(args[0], self.rng))
                    try:
                        updated = self._apply_one(character, 'admin', detail, money=delta)
                        lines.append(f'{character.name}: {delta:+d} → {self._money(updated.money)}')
                    except LedgerError as error:
                        lines.append(f'{character.name}: {error}')
            elif key in ('아이템추가', '아이템차감'):
                changes = parse_items(args[0])
                for character in targets:
                    if key == '아이템추가':
                        deltas = dict(changes)
                    else:
                        # 가진 만큼만 뺀다
                        deltas = {}
                        for name, count in changes.items():
                            owned = find_item_key(character.items, name)
                            if owned and character.items[owned] > 0:
                                deltas[owned] = -min(count, character.items[owned])
                    updated = self._apply_one(character, 'admin', detail, items=deltas) if deltas else character
                    lines.append(f'{character.name}: {describe_items(updated.items)}')
            else:
                # [힘 변경/..] → '힘' (뒷말은 서버 설정 stat_change, 띄어 쓰지 않아도 된다)
                stat = re.sub(r'\s*' + r'\s*'.join(map(re.escape, self.stat_change.replace(' ', ''))) + r'\s*$', '', head).strip()
                if any(character.stat_key(stat) is None for character in targets):
                    if any(normalize_key(name) == normalize_key(stat) for name in targets[0].statuses):
                        return f"'{stat}'{josa(stat, '은', '는')} 스탯으로 계산하는 상태 수치라 바꿀 수 없습니다."
                    return f"'{stat}' 스탯이 없습니다. 마스토돈 캐릭터 관리 > 스탯을 확인해 주세요."
                for character in targets:
                    stat_key = character.stat_key(stat)
                    before = int(character.stats[stat_key] or 0)
                    delta = amount(args[0], self.rng)
                    updated = self._apply_one(character, 'admin', detail, stats={stat_key: delta})
                    lines.append(f'{character.name}: {stat_key} {before} → {updated.stats.get(stat_key, before + delta)}')
        except DiceError as error:
            return str(error)

        if missing:
            lines.append(f"찾지 못한 캐릭터: {', '.join(missing)}")
        return '\n'.join([f'[{head}] 완료', ''] + lines)

    # ------------------------------------------------------------ 기본

    def _basic(self, request, key, head, args, caster):
        if key == HELP:
            return self._help(request.is_admin)
        topic = next((topic for topic in self._help_topics() if normalize_key(topic.command) == key), None)
        if topic is not None:
            return self._topic_help(topic, request.is_admin)

        if is_dice(head):
            try:
                return str(roll(head, self.rng))
            except DiceError as error:
                return str(error)

        if key == '랜덤':
            options = split_options('/'.join(args))
            if not options:
                return '형식: [랜덤/A, B, C]'
            return self.rng.choice(options)

        if key == '운세':
            rows = self.sheets.rows_or_empty('main', FORTUNE)
            if not rows:
                return None
            try:
                header_row, columns = header_index(rows, '문구')
            except LookupError:
                return None
            phrases = [cell(row, columns['문구']) for row in rows[header_row + 1:] if cell(row, columns['문구'])]
            if not phrases:
                return None
            # 같은 날 같은 사람은 같은 운세
            seed = hashlib.sha256(f'{request.user}|{self.clock():%Y-%m-%d}'.encode()).hexdigest()
            return render(phrases[int(seed, 16) % len(phrases)], caster, self.rng)
        return None

    def _help_command(self) -> str:
        return self._name(HELP)

    def _help_topics(self) -> List[HelpTopic]:
        if not self.ledger.available:
            return []
        return self.ledger.settings().help_topics or list(DEFAULT_HELP_TOPICS)

    def _help(self, is_admin: bool = False) -> str:
        # 맨 위 기본 도움말: 마스토돈 컨텐츠 설정 > 도움말 > 상점봇 (시트는 읽지 않는다), 서버가 없으면 기본 줄
        server_lines = self.ledger.settings().basic_help if self.ledger.available else []
        lines = list(server_lines) or list(DEFAULT_HELP)
        # 상점·운영진 명령어는 분류별 도움말로 나눈다 (줄은 마스토돈 서버가 준다). 컨텐츠 도움말은 시스템봇이 답한다
        topics = [f'[{topic.command}] {topic.description}'.strip() for topic in self._help_topics() if is_admin or not topic.admin]
        if topics:
            lines += ['', '분류별 도움말'] + topics
        return '\n'.join([self._help_command(), ''] + lines)

    def _topic_help(self, topic: HelpTopic, is_admin: bool) -> str:
        if topic.admin and not is_admin:
            return '운영진만 사용할 수 있는 명령어입니다.'
        server = self.ledger.settings()
        lines = {'shop': server.help, 'admin': server.admin_help}.get(topic.key, [])
        return '\n'.join([topic.command, ''] + (lines or ['아직 안내할 명령어가 없습니다.']))

    # ------------------------------------------------------------ 상점

    def _shop(self, request, key, head, args, caster):
        if key not in ('가방', '상점', '구매', '설명', '버리기'):
            return None
        if not self.ledger.available:
            return None

        # 상점 해금이 꺼져 있으면 (마스토돈 컨텐츠 설정 > 상점) 목록·구매만 막는다. 가방·설명·버리기는 그대로
        if key in ('상점', '구매') and not self.ledger.settings().shop_open:
            return '지금은 상점이 열려 있지 않습니다.'

        if key == '상점':
            items = [item for item in self.ledger.shop_items() if item.for_sale]
            if not items:
                return '판매 중인 아이템이 없습니다.'
            shops = list(dict.fromkeys(item.shop for item in items))
            lines = ['상점', '']
            for shop in shops:
                if len(shops) > 1:
                    lines.append(f'[{shop}]')
                lines += [f'- {item.name} ({self._money(item.price)})' for item in items if item.shop == shop]
            return '\n'.join(lines)

        if key == '설명':
            if not args or not args[0]:
                return '형식: [설명/아이템명]'
            item = self.ledger.find_shop_item(args[0])
            if item is None:
                return f"'{args[0]}'{josa(args[0], '은', '는')} 상점에 없는 아이템입니다."
            return f'[{item.name}]\n{render(item.description, caster, self.rng)}'

        character = self._character(request.user)
        if character is None:
            return self._not_registered()

        if key == '가방':
            return f'{character.name}의 가방\n\n소지금: {self._money(character.money)}\n소지품: {describe_items(character.items)}'

        if not args or not args[0]:
            return f'형식: [{head}/아이템명]'

        if key == '구매':
            item = self.ledger.find_shop_item(args[0])
            if item is None:
                return f"'{args[0]}'{josa(args[0], '은', '는')} 상점에 없는 아이템입니다."
            if not item.for_sale:
                return f'{item.name}{josa(item.name, "은", "는")} 구매할 수 없는 아이템입니다.'
            count = 1
            if len(args) > 1 and re.fullmatch(r'\d+', args[1]):
                count = max(1, int(args[1]))
            total = item.price * count
            if character.money < total:
                return f'소지금이 부족합니다. (필요: {self._money(total)}, 소지금: {self._money(character.money)})'
            updated = self._apply_one(character, 'purchase', f'{item.name} {count}개', money=-total, items={item.name: count})
            return f'{item.name} {with_unit(count)}{josa(with_unit(count), "을", "를")} 구매했습니다. (소지금: {self._money(updated.money)})'

        # 버리기: 효과 없이 1개를 없앤다
        existing = find_item_key(character.items, args[0])
        if existing is None or character.items[existing] <= 0:
            return f"'{args[0]}'{josa(args[0], '을', '를')} 가지고 있지 않습니다."
        updated = self._apply_one(character, 'consume', existing, items={existing: -1})
        left = updated.items.get(existing, 0)
        return f'{existing}{josa(existing, "을", "를")} 버렸습니다. (남은 수량: {left}개)'

    # ------------------------------------------------------------ 스탯

    def _stats(self, request, key, head, args, caster):
        if key not in ('상태확인', '상태', '사용'):
            return None
        if not self.ledger.available:
            return None

        character = self._character(request.user)
        if character is None:
            return self._not_registered()

        if key in ('상태확인', '상태'):
            lines = [f'{character.name}의 상태', '', f'소지금: {self._money(character.money)}',
                     f'소지품: {describe_items(character.items)}']
            lines += [f'{stat}: {value}' for stat, value in list(character.stats.items()) + list(character.statuses.items())
                      if value not in (None, '')]
            return '\n'.join(lines)

        if not args or not args[0]:
            return '형식: [사용/아이템명]'
        existing = find_item_key(character.items, args[0])
        if existing is None or character.items[existing] <= 0:
            return f"'{args[0]}'{josa(args[0], '을', '를')} 가지고 있지 않습니다."

        item = self.ledger.find_shop_item(existing)
        if item is not None and not item.usable:
            return f'{existing}{josa(existing, "은", "는")} 사용할 수 없는 아이템입니다.'

        # 효과는 모두 서버가 웹 인벤토리와 같은 규칙으로 처리한다 (상점 설정 > 아이템 효과)
        # [사용/아이템]  [사용/아이템/@상대]  [사용/아이템/소속 이름]  (소문내기는 시스템 계정에 DM)
        message, notify = self.ledger.use_item(request.user, existing, args=args[1:])
        # [사용/아이템/@상대] 의 @상대 는 마스토돈이 멘션으로 보지 않아서, 답글 앞에 태그해 상대에게 알린다
        tags = ' '.join(f'@{acct}' for acct in notify)
        return f'{tags} {message}'.strip()

    # ------------------------------------------------------------ 옵션

    def _options(self, request, key, head, args, caster):
        if key == '양도' and self.config.transfer_enabled:
            return self._transfer(request, head, args)
        if key == '출석' and self.config.attendance_reward > 0:
            return self._attendance(request)
        if key == normalize_key(self.config.randombox_command) and self.sheets.rows_or_empty('main', RANDOMBOX):
            return self._randombox(request, args)
        return None

    def _transfer(self, request, head, args):
        if not self.ledger.available:
            return None
        if len(args) < 2 or not args[0] or not args[1]:
            return f'형식: [양도/n{self.config.currency}/캐릭터명] 또는 [양도/아이템명/캐릭터명]'

        giver = self._character(request.user)
        if giver is None:
            return self._not_registered()
        matches = self.ledger.search(args[1])
        if not matches:
            return f"'{args[1]}' 캐릭터를 찾을 수 없습니다."
        if len(matches) > 1:
            return f"'{args[1]}'에 해당하는 캐릭터가 여럿입니다: {', '.join(c.name for c in matches)}. 더 자세히 적어 주세요."
        receiver = matches[0]
        if receiver.acct.lower() == giver.acct.lower():
            return '자기 자신에게는 양도할 수 없습니다.'

        money_match = re.fullmatch(rf'(\d+)\s*({re.escape(self.config.currency)})?', args[0].replace(',', ''))
        if money_match:
            value = int(money_match.group(1))
            if value <= 0:
                return '1 이상만 양도할 수 있습니다.'
            if giver.money < value:
                return f'소지금이 부족합니다. (소지금: {self._money(giver.money)})'
            result = self.ledger.apply([{'acct': giver.acct, 'money': -value}, {'acct': receiver.acct, 'money': value}],
                                       'transfer', f'{self._money(value)} → {receiver.name}')
            left = result.get(giver.acct.lower(), giver).money
            return f'{receiver.name}에게 {self._money(value)}{josa(self._money(value), "을", "를")} 양도했습니다. (소지금: {self._money(left)})'

        wanted = parse_items(args[0])
        if not wanted:
            return '양도할 아이템을 적어 주세요.'
        moved, take, give = [], {}, {}
        for name, count in wanted.items():
            existing = find_item_key(giver.items, name)
            if existing is None or giver.items[existing] < count:
                return f"'{name}'{josa(name, '이', '가')} {count}개 이상 있어야 양도할 수 있습니다."
            take[existing] = -count
            give[existing] = count
            moved.append(f'{existing} {count}개')
        self.ledger.apply([{'acct': giver.acct, 'items': take}, {'acct': receiver.acct, 'items': give}],
                          'transfer', f"{', '.join(moved)} → {receiver.name}")
        return f"{receiver.name}에게 {', '.join(moved)}{josa(moved[-1], '을', '를')} 양도했습니다."

    def _attendance(self, request):
        if not self.ledger.available:
            return None
        character = self._character(request.user)
        if character is None:
            return self._not_registered()
        reward = self._money(self.config.attendance_reward)
        try:
            updated = self._apply_one(character, 'attendance', f'+{reward}', money=self.config.attendance_reward, once_per_day=True)
        except LedgerError as error:
            if error.code == 'already_done':
                return '오늘은 이미 출석했습니다. 내일 다시 와 주세요!'
            raise
        return f'출석 완료! {reward}{josa(reward, "을", "를")} 받았습니다. (소지금: {self._money(updated.money)})'

    def _randombox(self, request, args):
        rows = self.sheets.rows_or_empty('main', RANDOMBOX)
        try:
            header_row, columns = header_index(rows, '박스명', '아이템')
        except LookupError:
            return '랜덤박스 시트 형식이 올바르지 않습니다. (박스명 | 가격 | 아이템 | 수량 | 확률)'

        boxes: dict = {}
        for row in rows[header_row + 1:]:
            box, item = cell(row, columns.get('박스명')), cell(row, columns.get('아이템'))
            if not box or not item:
                continue
            entry = boxes.setdefault(box, {'price': None, 'items': []})
            price = cell(row, columns.get('가격')).replace(',', '')
            if price.isdigit() and entry['price'] is None:
                entry['price'] = int(price)
            weight = cell(row, columns.get('확률')).rstrip('%') or '1'
            try:
                weight_value = float(weight)
            except ValueError:
                weight_value = 1.0
            entry['items'].append((item, cell(row, columns.get('수량')) or '1', max(weight_value, 0.0)))

        command = self.config.randombox_command
        if not args or not args[0]:
            listed = [f'- {name}' + (f' ({self._money(box["price"])})' if box['price'] is not None else '') for name, box in boxes.items()]
            return '\n'.join([command, ''] + (listed or ['열 수 있는 박스가 없습니다.']) + ['', f'[{command}/박스명]으로 열 수 있습니다.'])

        box_name = next((name for name in boxes if normalize_key(name) == normalize_key(args[0])), None)
        if box_name is None:
            return f"'{args[0]}' 박스를 찾을 수 없습니다."
        box = boxes[box_name]

        if not self.ledger.available:
            return None
        character = self._character(request.user)
        if character is None:
            return self._not_registered()

        money, items = 0, {}
        owned = find_item_key(character.items, box_name)
        if owned and character.items[owned] > 0:
            items[owned] = -1
            paid = f'{owned} 1개 사용'
        elif box['price'] is not None:
            if character.money < box['price']:
                return f"소지금이 부족합니다. (필요: {self._money(box['price'])}, 소지금: {self._money(character.money)})"
            money = -box['price']
            paid = f"{self._money(box['price'])} 사용"
        else:
            return f'{box_name}{josa(box_name, "이", "가")} 없습니다.'

        candidates = [entry for entry in box['items'] if entry[2] > 0] or box['items']
        item, count_text, _ = self.rng.choices(candidates, weights=[entry[2] or 1 for entry in candidates])[0]
        try:
            count = amount(count_text, self.rng)
        except DiceError:
            count = 1
        if count > 0:
            items[item] = items.get(item, 0) + count

        result = f"➭ '{item}' {count}개 획득" if count > 0 else f'➭ {item}'
        self._apply_one(character, 'randombox', f'{box_name} ({paid}) → {result[2:]}', money=money, items=items)
        return f'{box_name}{josa(box_name, "을", "를")} 열었습니다! ({paid})\n\n{result}'

    # ------------------------------------------------------------ 키워드

    def _keyword(self, command: str, head: str, caster: str) -> Optional[Reply]:
        rows = self.sheets.rows_or_empty('main', KEYWORDS)
        if not rows:
            return None
        try:
            header_row, columns = header_index(rows, '키워드', '답변')
        except LookupError:
            return None

        wanted = {normalize_key(command), normalize_key(head)}
        matches = []
        for row in rows[header_row + 1:]:
            keyword, answer = cell(row, columns['키워드']), cell(row, columns['답변'])
            if keyword and answer and normalize_key(keyword) in wanted:
                matches.append((answer, cell(row, columns.get('이미지'))))
        if not matches:
            return None

        answer, image = self.rng.choice(matches)
        media = [image] if image and self.config.keyword_images and image.startswith('http') else []
        return Reply(render(answer, caster, self.rng), media)
