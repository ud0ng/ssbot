"""명령어 처리. 툿의 [대괄호] 안 내용을 받아 답장 문구를 만든다.

처리 순서
  1. 운영진 명령어      [캐시 리셋] [소지금 추가/차감] [아이템 추가/차감] [OO 변경]
  2. 기본               [도움말] [NdM] [랜덤/A, B] [운세]
  3. 상점               [가방] [상점] [구매/템] [설명/템] [소모/템]
  4. 스탯               [상태 확인] [사용/템]
  5. 옵션               [양도/..] [출석] [랜덤박스/박스]
  6. 조사               [장소 목록] [진입/장소] [조사/포인트]
  7. 키워드 시트        운영진이 적은 키워드 답변
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

from . import game
from .config import Config, now_kst
from .game import Character, Ledger, find_item_key, format_items, parse_items, parse_stat_changes
from .investigation import Investigation
from .options import Recorder, effective
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
    def __init__(self, config: Config, sheets: Sheets, state: State,
                 rng: Optional[random.Random] = None, clock: Callable[[], datetime] = now_kst):
        self.base_config = config
        self.config = config
        self.sheets = sheets
        self.state = state
        self.rng = rng or random.Random()
        self.clock = clock
        self.ledger = Ledger(sheets)
        self.recorder = Recorder(sheets, clock)
        self.investigation = Investigation(config, sheets, state, self.ledger, self.rng, clock, self.recorder)

    def effective_config(self) -> Config:
        """설정 탭을 반영한 설정 (시트에서 바꾸면 다음 명령어부터 적용)."""
        return effective(self.base_config, self.sheets)

    # ------------------------------------------------------------ 입구

    def handle(self, request: Request) -> Optional[Reply]:
        parts = [part.strip() for part in request.command.split('/')]
        head = parts[0]
        key = normalize_key(head)
        args = parts[1:]
        caster = game.display_name(self.sheets, request.user, request.display_name)
        self.config = self.effective_config()
        self.investigation.config = self.config
        if self.config.is_admin(request.user):
            request.is_admin = True

        for handler in (self._admin, self._basic, self._shop, self._stats, self._options):
            reply = handler(request, key, head, args, caster)
            if reply is not None:
                return reply if isinstance(reply, Reply) else Reply(reply)

        reply = self.investigation.handle(request.user, key, args, caster)
        if reply is not None:
            return Reply(reply)

        reply = self._keyword(request.command, head, caster)
        if reply is not None:
            return reply

        return Reply(f'[{head}]{josa(head, "은", "는")} 없는 명령어입니다. [도움말]을 확인해 주세요.')

    # ------------------------------------------------------------ 공통

    def _character(self, user: str, fresh: bool = True) -> Optional[Character]:
        return self.ledger.find(user, fresh)

    def _not_registered(self) -> str:
        return '관리 시트에 등록된 캐릭터가 아닙니다. 운영진에게 문의해 주세요.'

    def _money(self, value: int) -> str:
        return f'{value}{self.config.currency}'

    # ------------------------------------------------------------ 운영진

    def _admin(self, request, key, head, args, caster):
        admin_keys = {'캐시리셋', '시트업데이트', '소지금추가', '소지금차감', '아이템추가', '아이템차감'}
        is_stat_change = key.endswith('변경') and len(key) > 2
        if key not in admin_keys and not is_stat_change:
            return None
        if not request.is_admin:
            return '운영진만 사용할 수 있는 명령어입니다.'

        if key in ('캐시리셋', '시트업데이트'):
            count = self.sheets.invalidate()
            return f'시트 캐시를 비웠습니다. ({count}개) 다음 명령어부터 시트 내용이 새로 반영됩니다.'

        if len(args) < 2:
            return f'형식: [{head}/값/캐릭터명] (캐릭터명은 쉼표로 여러 명, 또는 전원)'

        with self.sheets.write_lock:
            targets, missing = self.ledger.targets(args[1])
            if not targets:
                return f"캐릭터를 찾을 수 없습니다: {', '.join(missing) or args[1]}"

            lines = []
            try:
                if key in ('소지금추가', '소지금차감'):
                    sign = 1 if key == '소지금추가' else -1
                    for character in targets:
                        delta = sign * abs(amount(args[0], self.rng))
                        character.money += delta
                        self.ledger.save(character, money=True)
                        lines.append(f'{character.name}: {delta:+d} → {self._money(character.money)}')
                        self.recorder.record(character.name, character.acct, '운영진', f'소지금 {delta:+d} (@{request.user})')
                elif key in ('아이템추가', '아이템차감'):
                    sign = 1 if key == '아이템추가' else -1
                    changes = parse_items(args[0])
                    for character in targets:
                        for name, count in changes.items():
                            existing = find_item_key(character.items, name) or name
                            character.items[existing] = max(0, character.items.get(existing, 0) + sign * count)
                        self.ledger.save(character, items=True)
                        lines.append(f'{character.name}: {describe_items(character.items)}')
                        self.recorder.record(character.name, character.acct, '운영진', f'{head}: {args[0]} (@{request.user})')
                else:
                    stat = head[:-2].strip()
                    if any(character.stat_key(stat) is None for character in targets):
                        return f"관리 시트에 '{stat}' 스탯이 없습니다."
                    for character in targets:
                        stat_key = character.stat_key(stat)
                        before = game._to_int(character.stats[stat_key])
                        delta = amount(args[0], self.rng)
                        character.stats[stat_key] = str(before + delta)
                        self.ledger.save(character, stats=[stat_key])
                        lines.append(f'{character.name}: {stat_key} {before} → {before + delta}')
                        self.recorder.record(character.name, character.acct, '운영진', f'{stat_key} {delta:+d} (@{request.user})')
            except DiceError as error:
                return str(error)

        if missing:
            lines.append(f"찾지 못한 캐릭터: {', '.join(missing)}")
        return '\n'.join([f'[{head}] 완료', ''] + lines)

    # ------------------------------------------------------------ 기본

    def _basic(self, request, key, head, args, caster):
        if key == '도움말':
            return self._help()

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

    def _help(self) -> str:
        rows = self.sheets.rows_or_empty('main', HELP)
        lines = []
        if rows:
            try:
                header_row, columns = header_index(rows, '명령어')
                for row in rows[header_row + 1:]:
                    command = cell(row, columns['명령어'])
                    if command.startswith('['):
                        description = cell(row, columns.get('설명'))
                        lines.append(f'{command} {description}'.strip())
            except LookupError:
                pass
        return '\n'.join(['도움말', ''] + (lines or DEFAULT_HELP))

    # ------------------------------------------------------------ 상점

    def _shop(self, request, key, head, args, caster):
        if key not in ('가방', '상점', '구매', '설명', '소모'):
            return None
        if not self.ledger.available:
            return None

        if key == '상점':
            items = [item for item in game.shop_items(self.sheets) if item.for_sale]
            if not items:
                return '판매 중인 아이템이 없습니다.'
            return '\n'.join(['상점', ''] + [f'- {item.name} ({self._money(item.price)})' for item in items])

        if key == '설명':
            if not args or not args[0]:
                return '형식: [설명/아이템명]'
            item = game.find_shop_item(self.sheets, args[0])
            if item is None:
                return f"'{args[0]}'{josa(args[0], '은', '는')} 상점에 없는 아이템입니다."
            return f'[{item.name}]\n{render(item.description, caster, self.rng)}'

        with self.sheets.write_lock:
            character = self._character(request.user)
            if character is None:
                return self._not_registered()

            if key == '가방':
                return f'{character.name}의 가방\n\n소지금: {self._money(character.money)}\n소지품: {describe_items(character.items)}'

            if not args or not args[0]:
                return f'형식: [{head}/아이템명]'

            if key == '구매':
                item = game.find_shop_item(self.sheets, args[0])
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
                character.money -= total
                existing = find_item_key(character.items, item.name) or item.name
                character.items[existing] = character.items.get(existing, 0) + count
                self.ledger.save(character, money=True, items=True)
                self.recorder.record(character.name, character.acct, '구매', f'{item.name} {count}개 (-{self._money(total)})')
                return f'{item.name} {with_unit(count)}{josa(with_unit(count), "을", "를")} 구매했습니다. (소지금: {self._money(character.money)})'

            # 소모
            existing = find_item_key(character.items, args[0])
            if existing is None or character.items[existing] <= 0:
                return f"'{args[0]}'{josa(args[0], '을', '를')} 가지고 있지 않습니다."
            character.items[existing] -= 1
            self.ledger.save(character, items=True)
            self.recorder.record(character.name, character.acct, '소모', existing)
            return f'{existing}{josa(existing, "을", "를")} 소모했습니다. (남은 수량: {character.items[existing]}개)'

    # ------------------------------------------------------------ 스탯

    def _stats(self, request, key, head, args, caster):
        if key not in ('상태확인', '상태', '사용'):
            return None
        if not self.ledger.available:
            return None

        with self.sheets.write_lock:
            character = self._character(request.user)
            if character is None:
                return self._not_registered()

            if key in ('상태확인', '상태'):
                lines = [f'{character.name}의 상태', '', f'소지금: {self._money(character.money)}',
                         f'소지품: {describe_items(character.items)}']
                lines += [f'{stat}: {value}' for stat, value in character.stats.items() if value != '']
                return '\n'.join(lines)

            if not args or not args[0]:
                return '형식: [사용/아이템명]'
            existing = find_item_key(character.items, args[0])
            if existing is None or character.items[existing] <= 0:
                return f"'{args[0]}'{josa(args[0], '을', '를')} 가지고 있지 않습니다."

            item = game.find_shop_item(self.sheets, existing)
            if item is not None and not item.usable:
                return f'{existing}{josa(existing, "은", "는")} 사용할 수 없는 아이템입니다.'

            lines = [f'{existing}{josa(existing, "을", "를")} 사용했습니다.']
            changed_stats = []
            if item is not None and item.stat and item.value:
                stat_key = character.stat_key(item.stat)
                if stat_key is None:
                    return f"관리 시트에 '{item.stat}' 스탯이 없습니다. 운영진에게 문의해 주세요."
                try:
                    delta = amount(item.value, self.rng)
                except DiceError as error:
                    return str(error)
                before = game._to_int(character.stats[stat_key])
                character.stats[stat_key] = str(before + delta)
                changed_stats.append(stat_key)
                lines.append(f'➭ {stat_key} {delta:+d} ({before} → {before + delta})')

            character.items[existing] -= 1
            self.ledger.save(character, items=True, stats=changed_stats)
            self.recorder.record(character.name, character.acct, '사용', ' '.join([existing] + [line[2:] for line in lines[1:]]))
            return '\n'.join(lines)

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

        with self.sheets.write_lock:
            giver = self._character(request.user)
            if giver is None:
                return self._not_registered()
            receiver = self.ledger.find_by_name(args[1], fresh=True)
            if receiver is None:
                return f"'{args[1]}' 캐릭터를 찾을 수 없습니다."
            if receiver.row == giver.row:
                return '자기 자신에게는 양도할 수 없습니다.'

            money_match = re.fullmatch(rf'(\d+)\s*({re.escape(self.config.currency)})?', args[0].replace(',', ''))
            if money_match:
                value = int(money_match.group(1))
                if value <= 0:
                    return '1 이상만 양도할 수 있습니다.'
                if giver.money < value:
                    return f'소지금이 부족합니다. (소지금: {self._money(giver.money)})'
                giver.money -= value
                receiver.money += value
                self.ledger.save(giver, money=True)
                self.ledger.save(receiver, money=True)
                self.recorder.record(giver.name, giver.acct, '양도', f'{self._money(value)} → {receiver.name}')
                return f'{receiver.name}에게 {self._money(value)}{josa(self._money(value), "을", "를")} 양도했습니다. (소지금: {self._money(giver.money)})'

            wanted = parse_items(args[0])
            if not wanted:
                return '양도할 아이템을 적어 주세요.'
            for name, count in wanted.items():
                existing = find_item_key(giver.items, name)
                if existing is None or giver.items[existing] < count:
                    return f"'{name}'{josa(name, '이', '가')} {count}개 이상 있어야 양도할 수 있습니다."
            moved = []
            for name, count in wanted.items():
                existing = find_item_key(giver.items, name)
                giver.items[existing] -= count
                target = find_item_key(receiver.items, existing) or existing
                receiver.items[target] = receiver.items.get(target, 0) + count
                moved.append(f'{existing} {count}개')
            self.ledger.save(giver, items=True)
            self.ledger.save(receiver, items=True)
            self.recorder.record(giver.name, giver.acct, '양도', f"{', '.join(moved)} → {receiver.name}")
            return f"{receiver.name}에게 {', '.join(moved)}{josa(moved[-1], '을', '를')} 양도했습니다."

    def _attendance(self, request):
        if not self.ledger.available:
            return None
        today = f'{self.clock():%Y-%m-%d}'
        with self.sheets.write_lock:
            character = self._character(request.user)
            if character is None:
                return self._not_registered()
            if self.state.get('attendance', character.acct.lower()) == today:
                return '오늘은 이미 출석했습니다. 내일 다시 와 주세요!'
            character.money += self.config.attendance_reward
            self.ledger.save(character, money=True)
            self.state.set('attendance', character.acct.lower(), today)
            self.recorder.attendance(character.name, character.acct)
            self.recorder.record(character.name, character.acct, '출석', f'+{self._money(self.config.attendance_reward)}')
            reward = self._money(self.config.attendance_reward)
            return f'출석 완료! {reward}{josa(reward, "을", "를")} 받았습니다. (소지금: {self._money(character.money)})'

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

        with self.sheets.write_lock:
            character = self._character(request.user)
            if character is None:
                return self._not_registered()

            owned = find_item_key(character.items, box_name)
            if owned and character.items[owned] > 0:
                character.items[owned] -= 1
                paid = f'{owned} 1개 사용'
            elif box['price'] is not None:
                if character.money < box['price']:
                    return f"소지금이 부족합니다. (필요: {self._money(box['price'])}, 소지금: {self._money(character.money)})"
                character.money -= box['price']
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
                existing = find_item_key(character.items, item) or item
                character.items[existing] = character.items.get(existing, 0) + count
            self.ledger.save(character, money=True, items=True)

        result = f"➭ '{item}' {count}개 획득" if count > 0 else f'➭ {item}'
        self.recorder.record(character.name, character.acct, '랜덤박스', f'{box_name} ({paid}) → {result[2:]}')
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
