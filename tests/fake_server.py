"""테스트용 마스토돈 /api/v1/bot (메모리). 실제 서버(Game::Wallet)와 같은 규칙으로 돈·아이템·스탯을 바꾼다."""

from __future__ import annotations

import copy

from ssbot.server import LedgerError


def _key(name: str) -> str:
    return ''.join((name or '').split()).lower()


def _character(acct, name, money, items, health, mind):
    return {'acct': acct, 'name': name, 'money': money, 'items': [{'name': n, 'count': c, 'note': ''} for n, c in items],
            'stats': {'체력': health, '정신력': mind}}


class FakeServer:
    STAT_RANGE = (0, 1000)

    def __init__(self, today: str = '2026-10-11'):
        self.today = today
        self.config = {
            'currency': '코인', 'transfer_enabled': True, 'attendance_reward': 1, 'toot_reward_every': 0, 'toot_reward_amount': 1,
            'help': ['[가방] 현재 소지금과 소지품을 확인합니다.', '[출석] 하루 한 번 재화를 받습니다.'],
            'admin_help': ['[소지금 추가/10/캐릭터명] 소지금을 바꿉니다.'],
        }
        self.characters = [
            _character('manager', '운영자', 17, [('바나나', 2), ('사과', 1)], 90, 100),
            _character('Elena', '엘레나', 12, [('반지', 1)], 86, 30),
            _character('Sophia', '소피아', 1, [('아몬드', 2), ('파란 꽃병', 1)], 63, 54),
            _character('Lucas', '루카스', 3, [], 48, 28),
            _character('Maya', '마야', 20, [], 91, 93),
            _character('Grace', '그레이스', 15, [], 84, 59),
        ]
        self.shops = [{'name': '매점', 'description': '', 'items': [
            {'name': '반지', 'price': 3, 'description': '노란색 보석이 박힌 은색 반지다.', 'usable': True, 'permanent': False, 'stat': '정신력', 'value': '10'},
            {'name': '동화책', 'price': 1, 'description': '아이들이 읽을 법한 귀여운 동화책이다.', 'usable': True, 'stat': '체력', 'value': '5'},
            {'name': '사과', 'price': 1, 'description': '아삭아삭!', 'usable': True, 'stat': '체력', 'value': '1d10'},
            {'name': '바나나', 'price': 1, 'description': '부드럽고 달콤하고...', 'usable': True, 'stat': '정신력', 'value': '-(1d6+3)'},
            {'name': '아몬드', 'price': None, 'description': '사실은 청산가리입니다.', 'usable': True, 'stat': '정신력', 'value': '-20'},
            {'name': '축복 부적', 'price': None, 'description': '', 'usable': True, 'use_target': 'character', 'stat': '', 'value': ''},
            {'name': '나침반', 'price': 2, 'description': '', 'usable': True, 'permanent': True, 'stat': '정신력', 'value': '1'},
            {'name': '파란 꽃병', 'price': 10, 'description': '꽃을 꽂아둘 수 있습니다.', 'usable': False, 'stat': '', 'value': ''},
        ]}]
        self.records = []
        self.calls = []

    # ---- 조회

    def _json(self, character):
        stats = [{'name': name, 'value': value, 'type': 'stat', 'min': 0, 'max': 1000} for name, value in character['stats'].items()]
        # 상태 수치 (계산값, 바꿀 수 없음)
        stats.append({'name': '공격력', 'value': character['stats']['체력'] // 10, 'type': 'status'})
        return {'acct': character['acct'], 'name': character['name'], 'money': character['money'],
                'items': copy.deepcopy(character['items']), 'stats': stats}

    def get(self, path, params=None):
        self.calls.append(('GET', path))
        if path == 'config':
            return copy.deepcopy(self.config)
        if path == 'characters':
            return [self._json(character) for character in self.characters]
        if path == 'shops':
            return copy.deepcopy(self.shops)
        raise AssertionError(path)

    def find(self, acct):
        return next((c for c in self.characters if c['acct'].lower() == acct.lower()), None)

    # ---- 바꾸기

    def _use(self, json):
        """서버 Game::ItemUse::Runner 흉내: 대상(@아이디)이 있어야 하고 1개 소모, 대상을 notify 로 돌려준다."""
        target = next((arg for arg in json.get('args') or [] if arg), '')
        if not target:
            raise LedgerError('use_failed', '아이템을 쓸 캐릭터를 골라 주세요.')
        giver = self.find(json['acct'])
        entry = next((item for item in giver['items'] if _key(item['name']) == _key(json['name'])), None)
        entry['count'] -= 1
        giver['items'] = [item for item in giver['items'] if item['count'] > 0]
        acct = target.lstrip('@')
        return {'message': f"{acct}에게 {json['name']}을 사용했습니다.", 'notify': [acct] if acct.lower() != json['acct'].lower() else []}


    def post(self, path, json):
        self.calls.append(('POST', path))
        if path == 'items/use':
            return self._use(json)
        assert path == 'characters/apply'
        kind = json['kind']
        working = copy.deepcopy(self.characters)
        found = {c['acct'].lower(): c for c in working}
        if json.get('once_per_day') and any((r['acct'].lower(), r['kind'], r['day']) == (change['acct'].lower(), kind, self.today)
                                            for r in self.records for change in json['changes']):
            raise LedgerError('already_done', '오늘은 이미 했습니다.')

        for change in json['changes']:
            character = found.get(change['acct'].lower())
            if character is None:
                raise LedgerError('not_found', f"'{change['acct']}' 캐릭터를 찾을 수 없습니다.")
            money = character['money'] + int(change.get('money') or 0)
            if money < 0:
                raise LedgerError('insufficient_money', '소지금이 부족합니다.')
            character['money'] = money
            for name, delta in (change.get('items') or {}).items():
                entry = next((item for item in character['items'] if _key(item['name']) == _key(name)), None)
                count = (entry['count'] if entry else 0) + delta
                if count < 0:
                    raise LedgerError('insufficient_items', f"'{name}'이(가) 부족합니다.")
                if entry:
                    entry['count'] = count
                elif count > 0:
                    character['items'].append({'name': name, 'count': count, 'note': ''})
            character['items'] = [item for item in character['items'] if item['count'] > 0]
            for name, delta in (change.get('stats') or {}).items():
                stat = next((key for key in character['stats'] if _key(key) == _key(name)), None)
                if stat is None:
                    raise LedgerError('unknown_stat', f"'{name}' 스탯이 없습니다.")
                low, high = self.STAT_RANGE
                character['stats'][stat] = max(low, min(high, character['stats'][stat] + delta))

        self.characters = working
        for change in json['changes']:
            self.records.append({'acct': change['acct'], 'kind': kind, 'detail': json.get('detail', ''), 'day': self.today,
                                 'money': change.get('money', 0), 'items': change.get('items', {}), 'stats': change.get('stats', {})})
        return {'characters': [self._json(found[change['acct'].lower()]) for change in json['changes']]}
