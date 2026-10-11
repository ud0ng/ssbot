"""구글 시트 템플릿(.xlsx) 만들기.

  python tools/make_templates.py      → templates/*.xlsx

구글 드라이브에 올리면 구글 시트로 열린다. 내용은 tests/fixtures 의 예시 시트를 바탕으로 한다.
"""

from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures'
OUT = ROOT / 'templates'

HEADER_FILL = PatternFill('solid', fgColor='CFE2F3')
NOTE_FILL = PatternFill('solid', fgColor='FFF2CC')
HEADER_FONT = Font(bold=True)


def read(sheet: str, tab: str) -> list:
    with (FIXTURES / sheet / f'{tab}.csv').open(encoding='utf-8') as file:
        return [row for row in csv.reader(file)]


def add(workbook: Workbook, title: str, rows: list, header_row: int = 1, widths: dict | None = None, notes: tuple = ()) -> None:
    sheet = workbook.create_sheet(title)
    for row in rows:
        sheet.append(row)
    for cell in sheet[header_row]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
    for index in notes:
        for cell in sheet[index]:
            cell.fill = NOTE_FILL
    for row in sheet.iter_rows():
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical='top')
    for col in range(1, sheet.max_column + 1):
        letter = get_column_letter(col)
        longest = max((len(str(c.value or '').split('\n')[0]) for c in sheet[letter]), default=8)
        sheet.column_dimensions[letter].width = (widths or {}).get(letter, min(max(10, longest * 1.6), 70))
    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)


def new_workbook() -> Workbook:
    workbook = Workbook()
    workbook.remove(workbook.active)
    return workbook


def main_sheet() -> Workbook:
    workbook = new_workbook()

    help_rows = [['명령어', '설명']] + [[c, d] for c, d in [
        ('[도움말]', '도움말을 보여줍니다.'),
        ('[NdM]', 'M면체 주사위를 N개 굴립니다. 예: [1d100]'),
        ('[랜덤/A, B, C...]', '여러 옵션 중 1개를 골라 출력합니다.'),
        ('[운세]', '오늘의 운세를 확인합니다.'),
        ('[YN]', ''),
        ('[가위바위보]', ''),
        ('[홀짝]', ''),
        ('[현자의 조언]', ''),
        ('[가방]', '현재 소지금과 소지품을 확인합니다.'),
        ('[상점]', '상점 목록을 출력합니다.'),
        ('[구매/아이템명]', '원하는 아이템을 구매합니다. [구매/아이템명/수량]도 가능'),
        ('[설명/아이템명]', '아이템의 설명을 출력합니다.'),
        ('[소모/아이템명]', '인벤토리에서 아이템을 소모합니다. (스탯 반영 X)'),
        ('[상태 확인]', '소지금, 소지품, 스탯을 확인합니다.'),
        ('[사용/아이템명]', '아이템을 사용합니다. (스탯 반영 O)'),
        ('[양도/n코인/캐릭터명]', 'n코인을 캐릭터에게 양도합니다.'),
        ('[양도/아이템명/캐릭터명]', '아이템을 캐릭터에게 양도합니다.'),
        ('[출석]', '하루 한 번 출석 보상을 받습니다.'),
        ('[랜덤박스/박스명]', '랜덤박스를 엽니다. [랜덤박스]는 박스 목록'),
        ('[장소 목록]', '진입할 수 있는 장소 목록을 봅니다.'),
        ('[진입/장소명]', '장소에 들어갑니다.'),
        ('[조사/포인트명]', '지금 장소의 조사 포인트를 조사합니다.'),
        ('[쪽지시험]', '오늘의 쪽지시험 점수를 확인합니다.'),
        ('[낚시]', '강가에서 낚시합니다.'),
        ('[스킬]', '스킬을 사용합니다.'),
    ]]
    add(workbook, '도움말', help_rows, widths={'A': 26, 'B': 60})

    admin_rows = [['명령어', '설명', '예시'], *[[a, b, c] for a, b, c in [
        ('[캐시 리셋]', '시트를 고친 내용을 바로 반영합니다. (안 해도 1분 안에 반영)', '[캐시 리셋]'),
        ('[소지금 추가/금액/캐릭터명]', "캐릭터명은 쉼표로 여러 명, '전원'이면 모두", '[소지금 추가/10/겐지, 디바]'),
        ('[소지금 차감/금액/캐릭터명]', '', '[소지금 차감/10/전원]'),
        ('[ㅇㅇ 변경/수치/캐릭터명]', "관리 탭의 스탯을 바꿉니다. 수치에 주사위 가능", '[체력 변경/-1d6/겐지]'),
        ('[아이템 추가/템: n, 템: n/캐릭터명]', '', '[아이템 추가/사과: 2, 포도: 3/겐지]'),
        ('[아이템 차감/템: n/캐릭터명]', '', '[아이템 차감/사과: 1/전원]'),
        ('[스진/워크시트명]', 'STORY 계정에 DM 으로 보내면 스토리 시트의 그 워크시트를 송출', '[스진/1일차]'),
        ('[스진 중지]', '진행 중인 스토리를 멈춥니다.', '[스진 중지]'),
    ]]]
    add(workbook, '운영진 명령어', admin_rows, widths={'A': 34, 'B': 60, 'C': 34})

    add(workbook, '명단', read('main', '명단'), widths={'A': 16, 'B': 20})
    add(workbook, '관리', read('main', '관리'), widths={'A': 16, 'B': 20, 'C': 4, 'D': 10, 'E': 40})
    add(workbook, '상점', read('main', '상점'), widths={'A': 16, 'B': 10, 'C': 50, 'D': 12, 'E': 14})
    add(workbook, '운세', read('main', '운세'), widths={'A': 100})

    keywords = read('main', '키워드')
    keywords[0] = ['키워드', '답변', '이미지']
    keywords.insert(1, ['고양이', '야옹! {시전자}{을를} 쳐다본다.', '(이미지 주소를 넣으면 답변에 첨부됩니다. 예: https://…/cat.png)'])
    add(workbook, '키워드', keywords, widths={'A': 18, 'B': 70, 'C': 40})

    add(workbook, '랜덤박스', read('main', '랜덤박스'), widths={'A': 14, 'B': 8, 'C': 16, 'D': 8, 'E': 8})
    add(workbook, '설정', read('main', '설정'), widths={'A': 18, 'B': 16, 'C': 60})
    add(workbook, '출석부', read('main', '출석부'), widths={'A': 12, 'B': 8, 'C': 14, 'D': 16})
    add(workbook, '기록', read('main', '기록'), widths={'A': 20, 'B': 14, 'C': 16, 'D': 10, 'E': 60})
    workbook.move_sheet('설정', offset=-workbook.sheetnames.index('설정'))
    return workbook


def reservation_sheet() -> Workbook:
    workbook = new_workbook()
    rows = read('reservation', '관리')
    add(workbook, '관리', rows, widths={'A': 10, 'B': 8, 'C': 10, 'D': 8, 'E': 80})
    return workbook


def story_sheet() -> Workbook:
    workbook = new_workbook()
    add(workbook, '테스트', read('story', '테스트'), header_row=2, notes=(1,), widths={'A': 12, 'B': 8, 'C': 80})
    add(workbook, '예시 파트1', read('story', '예시 파트1'), widths={'A': 12, 'B': 8, 'C': 80})
    add(workbook, '빈칸', [['계정', '간격', '문구']], widths={'A': 12, 'B': 8, 'C': 80})
    return workbook


def investigation_sheet() -> Workbook:
    workbook = new_workbook()
    entry = read('investigation', '진입')
    add(workbook, '진입', entry, notes=(2, 3, 4), widths={'A': 14, 'B': 90, 'C': 14})
    for place in ('운동장', '교실', '음악실'):
        add(workbook, place, read('investigation', place), widths={'A': 14, 'B': 80, 'C': 22, 'D': 22})
    return workbook


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for name, build in (('1_기본_상점_스탯', main_sheet), ('2_예약툿', reservation_sheet),
                        ('3_스토리', story_sheet), ('4_조사', investigation_sheet)):
        path = OUT / f'{name}.xlsx'
        build().save(path)
        print('saved', path.name)


if __name__ == '__main__':
    main()
