"""툿 수 → 재화 자동 반영.

관리 시트의 캐릭터마다 마스토돈 툿 수를 주기적으로 확인해서
툿 N개를 새로 쓸 때마다 재화를 더한다. 처음 확인할 때는 기준만 잡고 주지 않는다.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from .game import Ledger
from .sheets import Sheets
from .state import State

logger = logging.getLogger(__name__)

CountLookup = Callable[[str], Optional[int]]   # acct → 툿 수 (모르면 None)


class TootRewards:
    def __init__(self, sheets: Sheets, state: State, ledger: Ledger, every: int, amount: int):
        self.sheets = sheets
        self.state = state
        self.ledger = ledger
        self.every = every
        self.amount = amount

    def run_once(self, lookup: CountLookup) -> dict:
        """보상한 캐릭터 이름 → 받은 재화."""
        if self.every <= 0 or not self.ledger.available:
            return {}
        rewarded = {}
        with self.sheets.write_lock:
            for character in self.ledger.characters(fresh=True):
                if not character.acct:
                    continue
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
                    character.money += steps * self.amount
                    self.ledger.save(character, money=True)
                    rewarded[character.name] = steps * self.amount
                self.state.set('toot_counts', key, count)
        if rewarded:
            logger.info('툿 수 보상: %s', rewarded)
        return rewarded
