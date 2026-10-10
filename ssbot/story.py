"""스토리 자동 진행.

스토리 시트의 워크시트 하나 = 한 파트(예: 1일차, 3일차 파트1)
  계정 | 간격 | 문구
  - 계정: .env 의 ACCOUNT_<이름> (NOTICE, STORY, SYSTEM ...)
  - 간격: 이 줄을 올리기 전에 기다릴 초
운영진(또는 봇 계정)이 STORY 계정에 DM 으로 [스진/워크시트명] → 순서대로 송출.
[스진 중지] 로 멈춘다.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from .sheets import Sheets, WorksheetNotFound, cell, header_index
from .text import normalize_key, render

logger = logging.getLogger(__name__)

SHEET = 'story'

Poster = Callable[[str, str, str], object]   # (계정 이름, 문구, 공개 범위)


class StoryRunner:
    def __init__(self, sheets: Sheets, poster: Poster, visibility: str = 'public'):
        self.sheets = sheets
        self.poster = poster
        self.visibility = visibility
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.current: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def lines(self, worksheet: str) -> list:
        title = next((t for t in self.sheets.titles(SHEET) if normalize_key(t) == normalize_key(worksheet)), worksheet)
        rows = self.sheets.rows(SHEET, title, fresh=True)
        header_row, columns = header_index(rows, '계정', '문구', search_rows=10)
        result = []
        for row in rows[header_row + 1:]:
            account, text = cell(row, columns['계정']), cell(row, columns['문구'])
            if not account or not text:
                continue
            try:
                interval = float(cell(row, columns.get('간격')) or 0)
            except ValueError:
                interval = 0.0
            result.append((account.upper(), max(interval, 0.0), text))
        return result

    def start(self, worksheet: str) -> str:
        if not self.sheets.has(SHEET):
            return '스토리 시트가 연결되어 있지 않습니다.'
        if self.running:
            return f"이미 '{self.current}' 스토리를 진행 중입니다. [스진 중지] 후 다시 시도해 주세요."
        try:
            lines = self.lines(worksheet)
        except WorksheetNotFound:
            return f"스토리 시트에 '{worksheet}' 워크시트가 없습니다."
        except LookupError:
            return f"'{worksheet}' 워크시트에서 계정 | 간격 | 문구 헤더를 찾을 수 없습니다."
        if not lines:
            return f"'{worksheet}' 워크시트에 진행할 문구가 없습니다."

        self._stop.clear()
        self.current = worksheet
        self._thread = threading.Thread(target=self._run, args=(worksheet, lines), daemon=True)
        self._thread.start()
        total = sum(interval for _, interval, _ in lines)
        return f"'{worksheet}' 스토리 진행을 시작합니다. ({len(lines)}줄, 약 {int(total // 60)}분 {int(total % 60)}초)"

    def stop(self) -> str:
        if not self.running:
            return '진행 중인 스토리가 없습니다.'
        self._stop.set()
        return f"'{self.current}' 스토리 진행을 멈췄습니다."

    def _run(self, worksheet: str, lines: list) -> None:
        logger.info('스토리 시작: %s (%d줄)', worksheet, len(lines))
        for index, (account, interval, text) in enumerate(lines, start=1):
            if self._stop.wait(interval):
                logger.info('스토리 중지: %s (%d/%d)', worksheet, index - 1, len(lines))
                break
            try:
                self.poster(account, render(text), self.visibility)
            except Exception:
                logger.exception('스토리 송출 실패: %s %d줄 (%s)', worksheet, index, account)
        else:
            logger.info('스토리 완료: %s', worksheet)
        self.current = None
