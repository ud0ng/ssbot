"""봇이 기억해야 하는 작은 상태를 JSON 파일에 저장한다.

  attendance      아이디 → 마지막 출석 날짜
  investigations  날짜 → {아이디: 오늘 조사 횟수}
  places          아이디 → 지금 진입한 장소
  toot_counts     아이디 → 마지막으로 확인한 툿 수
  reservations    이미 보낸 예약 툿 키
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class State:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding='utf-8')
        temporary.replace(self.path)

    def get(self, section: str, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(section, {}).get(key, default)

    def set(self, section: str, key: str, value: Any) -> None:
        with self._lock:
            self._data.setdefault(section, {})[key] = value
            self._save()

    def section(self, section: str) -> dict:
        with self._lock:
            return dict(self._data.get(section, {}))

    def replace_section(self, section: str, value: dict) -> None:
        with self._lock:
            self._data[section] = value
            self._save()

    def increment_daily(self, section: str, day: str, key: str) -> int:
        """날짜별 횟수를 1 올리고 새 값을 돌려준다. 지난 날짜는 지운다."""
        with self._lock:
            days = self._data.setdefault(section, {})
            for old in [d for d in days if d != day]:
                del days[old]
            counts = days.setdefault(day, {})
            counts[key] = counts.get(key, 0) + 1
            self._save()
            return counts[key]

    def daily(self, section: str, day: str, key: str) -> int:
        with self._lock:
            return self._data.get(section, {}).get(day, {}).get(key, 0)
