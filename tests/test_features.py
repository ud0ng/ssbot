import csv
import random
from datetime import datetime, timedelta

from ssbot.config import KST
from ssbot.reservation import ReservationScheduler, parse_visibility, parse_when
from ssbot.rewards import TootRewards
from ssbot.sheets import CsvBackend, Sheets
from ssbot.state import State
from ssbot.story import StoryRunner


# ------------------------------------------------------------ 조사

def test_place_list_hides_closed(bot):
    text = bot('장소 목록')
    assert '- 운동장' in text and '- 음악실' in text
    assert '교무실' not in text and '슬래시' not in text


def test_investigate_requires_entry(bot):
    assert '먼저 [진입/장소명]' in bot('조사/모래밭')


def test_enter_and_investigate_applies_effects(bot):
    entered = bot('진입/운동장')
    assert '{' not in entered
    text = bot('조사/모래밭')
    assert text.startswith('[모래밭]')
    assert "➭ '기념주화' 1개 획득" in text and '➭ 체력 -3' in text and '➭ 정신력 -1' in text
    elena = bot.server.find('Elena')
    assert [(item['name'], item['count']) for item in elena['items']] == [('반지', 1), ('기념주화', 1)]
    assert elena['stats'] == {'체력': 83, '정신력': 29}
    assert bot.server.records[-1]['kind'] == 'investigation'


def test_closed_place_and_unknown_point(bot):
    assert '진입할 수 없습니다' in bot('진입/교무실')
    bot('진입/교실')
    assert "'철봉'이라는 조사 포인트가 없습니다" in bot('조사/철봉')


def test_daily_investigation_limit(bot, sheet_dir):
    path = sheet_dir / 'main' / '설정.csv'
    path.write_text(path.read_text(encoding='utf-8').replace('하루 조사 횟수,0', '하루 조사 횟수,1'), encoding='utf-8')
    bot('진입/교실')
    assert '(오늘 조사 1/1회)' in bot('조사/책상')
    assert '모두 사용했습니다' in bot('조사/칠판')


# ------------------------------------------------------------ 스토리

def test_story_lines_and_run(sheet_dir):
    posted = []
    runner = StoryRunner(Sheets(CsvBackend(sheet_dir), ttl=0), lambda account, text, visibility: posted.append((account, text, visibility)))
    lines = runner.lines('테스트')
    assert lines[0] == ('NOTICE', 5.0, '✧ 잠시 스토리 계정 송출 테스트를 진행합니다.')
    assert [account for account, _, _ in lines] == ['NOTICE', 'STORY', 'STORY', 'STORY']

    runner.lines = lambda worksheet: [(a, 0.0, t) for a, _, t in lines]   # 간격 없이
    assert '시작합니다' in runner.start('테스트')
    runner._thread.join(5)
    assert [p[0] for p in posted] == ['NOTICE', 'STORY', 'STORY', 'STORY']
    del runner.lines                                                      # 원래 시트 읽기로
    assert '없습니다' in runner.start('없는시트')


# ------------------------------------------------------------ 예약 툿

def test_parse_when_and_visibility():
    now = datetime(2026, 8, 1, 0, 5, tzinfo=KST)
    assert parse_when('08/01', '0:02', now) == datetime(2026, 8, 1, 0, 2, tzinfo=KST)
    assert parse_when('2026-08-01', '오후 10:01', now) == datetime(2026, 8, 1, 22, 1, tzinfo=KST)
    assert parse_when('', '0:02', now) is None
    assert parse_visibility('디엠') == 'direct' and parse_visibility('일반') == 'public'


def test_reservations_post_once(sheet_dir, tmp_path):
    posted = []
    scheduler = ReservationScheduler(Sheets(CsvBackend(sheet_dir), ttl=0), State(tmp_path / 's.json'),
                                     lambda account, text, visibility: posted.append((account, visibility, text)))
    now = datetime(2026, 8, 1, 0, 3, tzinfo=KST)
    assert scheduler.tick(now) == 2
    assert posted[0] == ('NOTICE', 'public', '✧ 밤이 되었습니다. 마피아는 고개를 들어 주시기 바랍니다.')
    assert posted[1][:2] == ('SYSTEM', 'direct')
    assert scheduler.tick(now + timedelta(seconds=20)) == 0
    # 한참 지난 예약은 보내지 않는다
    assert scheduler.tick(datetime(2026, 8, 1, 23, 59, tzinfo=KST)) == 0


# ------------------------------------------------------------ 툿 수 보상

def test_toot_rewards_high_water_mark(tmp_path, ledger, server):
    rewards = TootRewards(State(tmp_path / 's.json'), ledger, every=10, amount=2)
    counts = {'elena': 5}
    lookup = lambda acct: counts.get(acct.lower())
    assert rewards.run_once(lookup) == {}               # 기준만 잡는다
    counts['elena'] = 25
    assert rewards.run_once(lookup) == {'엘레나': 4}    # 10, 20 두 번 넘김
    assert server.find('Elena')['money'] == 16
    assert server.records[-1]['kind'] == 'toot'
    counts['elena'] = 12                                 # 지움
    assert rewards.run_once(lookup) == {}
    counts['elena'] = 29
    assert rewards.run_once(lookup) == {}               # 최고치(25)를 넘지 않았으니 없음


# ------------------------------------------------------------ 설정 탭 · 서버 재화 규칙

def _set(sheet_dir, item, value):
    path = sheet_dir / 'main' / '설정.csv'
    rows = list(csv.reader(path.open(encoding='utf-8')))
    for row in rows:
        if row[0] == item:
            row[1] = value
    with path.open('w', encoding='utf-8', newline='') as file:
        csv.writer(file).writerows(rows)


def test_currency_rules_come_from_the_server(bot, server):
    server.config['currency'] = '골드'
    assert '소지금: 12골드' in bot('가방')
    server.config['attendance_reward'] = 0
    assert '없는 명령어' in bot('출석')
    server.config['transfer_enabled'] = False
    assert '없는 명령어' in bot('양도/1/소피아')


def test_settings_tab_admins(bot, sheet_dir):
    assert '운영진만' in bot('소지금 추가/1/엘레나', user='Sophia')
    _set(sheet_dir, '운영진', 'Sophia, 다른사람')
    assert '완료' in bot('소지금 추가/1/엘레나', user='Sophia')
