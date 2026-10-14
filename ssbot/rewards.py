"""툿 수 → 재화 자동 반영.

마스토돈 캐릭터 관리의 캐릭터마다 툿 수를 주기적으로 확인해서
툿 N개를 새로 쓸 때마다 재화를 더한다 (기준·재화는 마스토돈 컨텐츠 설정 > 상점의 재화 규칙).
처음 확인할 때는 기준만 잡고 주지 않는다.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from .server import LedgerError, ServerLedger
from .state import State

logger = logging.getLogger(__name__)

CountLookup = Callable[[str], Optional[int]]   # acct → 툿 수 (모르면 None)


class TootRewards:
    def __init__(self, state: State, ledger: ServerLedger, every: int = 0, amount: int = 1):
        self.state = state
        self.ledger = ledger
        self.every = every
        self.amount = amount

    def run_once(self, lookup: CountLookup) -> dict:
        """보상한 캐릭터 이름 → 받은 재화."""
        if self.every <= 0 or not self.ledger.available:
            return {}
        rewarded = {}
        for character in self.ledger.characters(fresh=True):
            key = character.acct.lower()
            count = lookup(character.acct)
            if count is None:
                continue
            # 지금까지의 최고 툿 수를 기준으로 한다 (지웠다가 다시 써서 보상을 또 받지 않게)
            last = self.state.get('toot_counts', key)
            if last is None:
                self.state.set('toot_counts', key, count)
                continue
            if count <= last:
                continue
            steps = count // self.every - last // self.every
            if steps > 0:
                try:
                    self.ledger.apply([{'acct': character.acct, 'money': steps * self.amount}], 'toot',
                                      f'툿 {count}개 → +{steps * self.amount}')
                    rewarded[character.name] = steps * self.amount
                except LedgerError:
                    logger.exception('툿 보상 실패: %s', character.acct)
                    continue
            self.state.set('toot_counts', key, count)
        if rewarded:
            logger.info('툿 수 보상: %s', rewarded)
        return rewarded
