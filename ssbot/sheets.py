"""구글 스프레드시트 읽기/쓰기.

시트는 네 종류이고 각각 따로 연결한다 (없으면 그 기능만 꺼진다).
  main           기본·상점·스탯 (도움말, 명단, 관리, 상점, 운세, 키워드, 랜덤박스)
  reservation    예약 툿
  story          스토리 자동 진행
  investigation  조사

읽기는 짧은 시간 캐시한다. 운영진이 시트를 고친 뒤 바로 반영하려면 [캐시 리셋].
"""

from __future__ import annotations

import csv
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Protocol

Rows = List[List[str]]


class WorksheetNotFound(LookupError):
    pass


class Backend(Protocol):
    def has_sheet(self, sheet: str) -> bool: ...
    def worksheet_titles(self, sheet: str) -> List[str]: ...
    def values(self, sheet: str, worksheet: str) -> Rows: ...
    def update_cells(self, sheet: str, worksheet: str, cells: Dict[tuple, str]) -> None: ...


SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


class GoogleBackend:
    """구글 시트에 접속한다. sheet_ids: {'main': '시트ID', ...}

    credentials.json(서비스 계정 키)이 있으면 그것을, 없으면 이 서버(GCP VM)에 붙어 있는
    서비스 계정을 쓴다. 키 발급이 막힌 프로젝트에서도 VM 의 API 액세스 범위에
    Google Sheets 를 허용하면 동작한다.
    """

    def __init__(self, credentials_path: str, sheet_ids: Dict[str, str]):
        import gspread

        self._gspread = gspread
        if credentials_path and Path(credentials_path).exists():
            self._client = gspread.service_account(filename=credentials_path)
            self.identity = 'credentials.json'
        else:
            import google.auth

            credentials, _ = google.auth.default(scopes=SCOPES)
            self._client = gspread.authorize(credentials)
            self.identity = getattr(credentials, 'service_account_email', 'VM 서비스 계정')
        self._ids = {key: value for key, value in sheet_ids.items() if value}
        self._spreadsheets: Dict[str, object] = {}
        self._lock = threading.Lock()

    def has_sheet(self, sheet: str) -> bool:
        return sheet in self._ids

    def _spreadsheet(self, sheet: str):
        with self._lock:
            if sheet not in self._spreadsheets:
                self._spreadsheets[sheet] = self._client.open_by_key(self._ids[sheet])
            return self._spreadsheets[sheet]

    def worksheet_titles(self, sheet: str) -> List[str]:
        return [worksheet.title for worksheet in self._spreadsheet(sheet).worksheets()]

    def _worksheet(self, sheet: str, worksheet: str):
        try:
            return self._spreadsheet(sheet).worksheet(worksheet)
        except self._gspread.exceptions.WorksheetNotFound as error:
            raise WorksheetNotFound(worksheet) from error

    def values(self, sheet: str, worksheet: str) -> Rows:
        return self._worksheet(sheet, worksheet).get_all_values()

    def update_cells(self, sheet: str, worksheet: str, cells: Dict[tuple, str]) -> None:
        if not cells:
            return
        target = self._worksheet(sheet, worksheet)
        target.update_cells(
            [self._gspread.Cell(row, col, value) for (row, col), value in cells.items()],
            value_input_option='USER_ENTERED',
        )


class CsvBackend:
    """폴더의 CSV 파일을 시트처럼 쓴다 (테스트·로컬 확인용). root/<sheet>/<worksheet>.csv"""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._lock = threading.Lock()

    def has_sheet(self, sheet: str) -> bool:
        return (self.root / sheet).is_dir()

    def worksheet_titles(self, sheet: str) -> List[str]:
        return sorted(path.stem for path in (self.root / sheet).glob('*.csv'))

    def _path(self, sheet: str, worksheet: str) -> Path:
        path = self.root / sheet / f'{worksheet}.csv'
        if not path.exists():
            raise WorksheetNotFound(worksheet)
        return path

    def values(self, sheet: str, worksheet: str) -> Rows:
        with self._lock, self._path(sheet, worksheet).open(encoding='utf-8', newline='') as file:
            return [list(row) for row in csv.reader(file)]

    def update_cells(self, sheet: str, worksheet: str, cells: Dict[tuple, str]) -> None:
        with self._lock:
            path = self._path(sheet, worksheet)
            with path.open(encoding='utf-8', newline='') as file:
                rows = [list(row) for row in csv.reader(file)]
            for (row, col), value in cells.items():
                while len(rows) < row:
                    rows.append([])
                while len(rows[row - 1]) < col:
                    rows[row - 1].append('')
                rows[row - 1][col - 1] = str(value)
            with path.open('w', encoding='utf-8', newline='') as file:
                csv.writer(file).writerows(rows)


class Sheets:
    """백엔드 + 캐시."""

    def __init__(self, backend: Backend, ttl: float = 60.0):
        self.backend = backend
        self.ttl = ttl
        self._cache: Dict[tuple, tuple] = {}
        self._titles: Dict[str, tuple] = {}
        self._lock = threading.RLock()
        # 관리 시트를 고치는 명령이 겹치지 않게 (읽기 → 계산 → 쓰기)
        self.write_lock = threading.RLock()

    def has(self, sheet: str) -> bool:
        return self.backend.has_sheet(sheet)

    def titles(self, sheet: str) -> List[str]:
        with self._lock:
            cached = self._titles.get(sheet)
            if cached and cached[0] > time.time():
                return cached[1]
        titles = self.backend.worksheet_titles(sheet)
        with self._lock:
            self._titles[sheet] = (time.time() + self.ttl, titles)
        return titles

    def rows(self, sheet: str, worksheet: str, fresh: bool = False) -> Rows:
        key = (sheet, worksheet)
        if not fresh:
            with self._lock:
                cached = self._cache.get(key)
                if cached and cached[0] > time.time():
                    return cached[1]
        values = self.backend.values(sheet, worksheet)
        with self._lock:
            self._cache[key] = (time.time() + self.ttl, values)
        return values

    def rows_or_empty(self, sheet: str, worksheet: str, fresh: bool = False) -> Rows:
        if not self.has(sheet):
            return []
        try:
            return self.rows(sheet, worksheet, fresh)
        except WorksheetNotFound:
            return []

    def update(self, sheet: str, worksheet: str, cells: Dict[tuple, str]) -> None:
        self.backend.update_cells(sheet, worksheet, cells)
        self.invalidate(sheet, worksheet)

    def invalidate(self, sheet: Optional[str] = None, worksheet: Optional[str] = None) -> int:
        with self._lock:
            keys = [key for key in self._cache if (sheet is None or key[0] == sheet) and (worksheet is None or key[1] == worksheet)]
            for key in keys:
                del self._cache[key]
            if worksheet is None:
                for title_key in [k for k in self._titles if sheet is None or k == sheet]:
                    del self._titles[title_key]
            return len(keys)


def header_index(rows: Rows, *names: str, search_rows: int = 5) -> tuple[int, Dict[str, int]]:
    """앞쪽 몇 줄에서 names 가 모두 들어 있는 헤더 행을 찾는다. (행 번호 0-based, {이름: 열})"""
    for index, row in enumerate(rows[:search_rows]):
        cleaned = [cell.strip() for cell in row]
        if all(name in cleaned for name in names):
            return index, {cell: col for col, cell in enumerate(cleaned) if cell}
    raise LookupError(f"헤더 {', '.join(names)} 를 찾을 수 없습니다.")


def cell(row: List[str], col: Optional[int]) -> str:
    if col is None or col >= len(row):
        return ''
    return (row[col] or '').strip()
