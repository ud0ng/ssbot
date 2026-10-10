import csv

from ssbot.commands import first_command
from ssbot.game import Ledger


def ledger_row(sheet_dir, name):
    with (sheet_dir / 'main' / '관리.csv').open(encoding='utf-8') as file:
        rows = list(csv.reader(file))
    header = rows[0]
    row = next(r for r in rows if r[0] == name)
    return dict(zip(header, row))


def test_first_command():
    assert first_command('@BOT 오늘의 운세는 몇 점? [1d100]') == '1d100'
    assert first_command('그냥 대화') is None


def test_help_lists_sheet_commands(bot):
    text = bot('도움말')
    assert text.startswith('도움말\n\n')
    assert '[가방] 현재 소지금과 소지품을 확인합니다.' in text
    assert '그 외에도' not in text


def test_dice_and_random(bot):
    assert 1 <= int(bot('1d100')) <= 100
    assert bot('랜덤/사과, 배, 감') in ('사과', '배', '감')


def test_fortune_is_stable_per_day(bot):
    assert bot('운세') == bot('운세')


def test_keyword_with_tags_uses_roster_name(bot):
    answer = bot('허기')
    assert '엘레나' in answer and '{' not in answer


def test_keyword_exact_with_colon(bot):
    assert bot('궁극기:겐지').startswith('겐지가 궁극기 <용검>')


def test_unknown_command(bot):
    assert '없는 명령어' in bot('없는명령')


def test_bag_and_status(bot):
    assert '소지금: 12코인' in bot('가방')
    status = bot('상태 확인')
    assert '체력: 86' in status and '정신력: 30' in status


def test_shop_lists_only_for_sale(bot):
    text = bot('상점')
    assert '- 반지 (3코인)' in text
    assert '아몬드' not in text


def test_buy_updates_sheet(bot, sheet_dir):
    assert '사과 2개를 구매했습니다' in bot('구매/사과/2')
    row = ledger_row(sheet_dir, '엘레나')
    assert row['소지금'] == '10'
    assert row['소지품'] == '반지: 1, 사과: 2'


def test_buy_not_for_sale_and_too_expensive(bot):
    assert '구매할 수 없는' in bot('구매/아몬드')
    assert '소지금이 부족합니다' in bot('구매/파란 꽃병/2')


def test_use_applies_stat(bot, sheet_dir):
    text = bot('사용/반지')
    assert '➭ 정신력 +10 (30 → 40)' in text
    row = ledger_row(sheet_dir, '엘레나')
    assert row['정신력'] == '40' and row['소지품'] == ''


def test_use_unusable(bot):
    bot('아이템 추가/파란 꽃병: 1/엘레나', user='manager', admin=True)
    assert '사용할 수 없는' in bot('사용/파란 꽃병')


def test_consume_does_not_change_stats(bot, sheet_dir):
    assert '소모했습니다' in bot('소모/반지')
    assert ledger_row(sheet_dir, '엘레나')['정신력'] == '30'


def test_transfer_money_and_items(bot, sheet_dir):
    assert '소피아에게 5코인을 양도했습니다' in bot('양도/5코인/소피아')
    assert ledger_row(sheet_dir, '소피아')['소지금'] == '6'
    assert '소피아에게 반지 1개를 양도했습니다' in bot('양도/반지/소피아')
    assert '반지: 1' in ledger_row(sheet_dir, '소피아')['소지품']
    assert '자기 자신' in bot('양도/1/엘레나')


def test_attendance_once_per_day(bot, sheet_dir):
    assert '출석 완료' in bot('출석')
    assert '이미 출석' in bot('출석')
    assert ledger_row(sheet_dir, '엘레나')['소지금'] == '13'


def test_randombox(bot, sheet_dir):
    listing = bot('랜덤박스')
    assert '행운 상자 (5코인)' in listing
    text = bot('랜덤박스/행운 상자')
    assert '열었습니다' in text
    assert ledger_row(sheet_dir, '엘레나')['소지금'] == '7'
    assert '보물 상자가 없습니다' in bot('랜덤박스/보물 상자')


def test_admin_commands(bot, sheet_dir):
    assert '운영진만' in bot('소지금 추가/10/겐지')
    text = bot('소지금 추가/10/엘레나, 소피아', user='manager', admin=True)
    assert '엘레나: +10 → 22코인' in text
    assert ledger_row(sheet_dir, '소피아')['소지금'] == '11'
    text = bot('체력 변경/-10/전원', user='manager', admin=True)
    assert '마야: 체력 91 → 81' in text
    assert "'용기' 스탯이 없습니다" in bot('용기 변경/1/전원', user='manager', admin=True)
    bot('아이템 추가/사과: 2, 포도: 3/루카스', user='manager', admin=True)
    assert ledger_row(sheet_dir, '루카스')['소지품'] == '사과: 2, 포도: 3'
    bot('아이템 차감/사과: 1/루카스', user='manager', admin=True)
    assert ledger_row(sheet_dir, '루카스')['소지품'] == '사과: 1, 포도: 3'
    assert '찾지 못한 캐릭터: 없는사람' in bot('소지금 차감/1/엘레나, 없는사람', user='manager', admin=True)


def test_not_registered(bot):
    assert '등록된 캐릭터가 아닙니다' in bot('가방', user='stranger')


def test_ledger_skips_blank_column(bot):
    ledger = Ledger(bot.sheets)
    character = ledger.find('Elena')
    assert character.money == 12
    assert list(character.stats) == ['체력', '정신력']
