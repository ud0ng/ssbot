from ssbot.commands import first_command


def character(bot, acct):
    found = bot.server.find(acct)
    return {**found, 'items': {item['name']: item['count'] for item in found['items']}}


def test_first_command():
    assert first_command('@BOT 오늘의 운세는 몇 점? [1d100]') == '1d100'
    assert first_command('그냥 대화') is None


def test_help_lists_basic_lines_and_topics(bot):
    text = bot('도움말')
    assert text.startswith('도움말\n\n')
    assert '[NdM] M면체 주사위를 N개 굴립니다.' in text
    assert '그 외에도' not in text
    # 상점·운영진 명령어는 분류별 도움말로 안내만 (컨텐츠 도움말은 시스템봇이 답한다)
    assert '[가방]' not in text
    assert '[상점 도움말]' in text and '[컨텐츠 도움말]' not in text
    assert '[운영진 도움말]' not in text
    assert '[운영진 도움말]' in bot('도움말', user='manager')


def test_topic_help(bot):
    shop = bot('상점 도움말')
    assert shop.startswith('상점 도움말\n\n')
    assert '[가방] 현재 소지금과 소지품을 확인합니다.' in shop and '[출석] 하루 한 번 재화를 받습니다.' in shop
    assert bot('운영진 도움말') == '운영진만 사용할 수 있는 명령어입니다.'
    assert '[소지금 추가/10/캐릭터명]' in bot('운영진 도움말', user='manager')


def test_help_follows_mastodon_settings(bot, server):
    server.config.update({
        'help_command': '안내',
        'basic_help': ['[NdM] 주사위', '[운세] 오늘의 운세'],
        'help_topics': [{'key': 'shop', 'command': '가게 안내', 'description': '가게 명령어'},
                        {'key': 'admin', 'command': '운영 안내', 'description': '운영 명령어', 'admin': True}],
    })
    text = bot('안내')
    assert text.startswith('안내\n\n[NdM] 주사위\n[운세] 오늘의 운세\n')
    assert '[가게 안내] 가게 명령어' in text and '[운영 안내]' not in text
    assert bot('가게 안내').startswith('가게 안내\n\n[가방]')
    assert '[소지금 추가' in bot('운영 안내', user='manager')
    assert bot('상점 도움말').endswith('[안내]를 확인해 주세요.')


def test_dice_and_random(bot):
    assert 1 <= int(bot('1d100')) <= 100
    assert bot('랜덤/사과, 배, 감') in ('사과', '배', '감')


def test_fortune_is_stable_per_day(bot):
    assert bot('운세') == bot('운세')


def test_keyword_with_tags_uses_character_name(bot):
    answer = bot('허기')
    assert '엘레나' in answer and '{' not in answer


def test_keyword_exact_with_colon(bot):
    assert bot('궁극기:겐지').startswith('겐지가 궁극기 <용검>')


def test_unknown_command(bot):
    assert '없는 명령어' in bot('없는명령')


def test_bag_and_status(bot):
    assert '소지금: 12코인' in bot('가방')
    status = bot('상태 확인')
    assert '체력: 86' in status and '정신력: 30' in status and '공격력: 8' in status


def test_shop_lists_only_for_sale(bot):
    text = bot('상점')
    assert '- 반지 (3코인)' in text
    assert '아몬드' not in text


def test_buy_updates_server(bot):
    assert '사과 2개를 구매했습니다 (소지금: 10코인)' in bot('구매/사과/2').replace('.', '')
    elena = character(bot, 'Elena')
    assert elena['money'] == 10 and elena['items'] == {'반지': 1, '사과': 2}
    assert bot.server.records[-1]['kind'] == 'purchase'


def test_buy_not_for_sale_and_too_expensive(bot):
    assert '구매할 수 없는' in bot('구매/아몬드')
    assert '소지금이 부족합니다' in bot('구매/파란 꽃병/2')


def test_use_unusable(bot):
    bot('아이템 추가/파란 꽃병: 1/엘레나', user='manager', admin=True)
    assert '사용할 수 없는' in bot('사용/파란 꽃병')


def test_use_goes_to_the_server_and_tags_the_target(bot):
    """효과(스탯·재화·누적 수치·효과)는 서버가 처리한다. [사용/아이템/@상대] 의 상대는 답글 앞에 태그해 알린다."""
    bot('아이템 추가/축복 부적: 1/엘레나', user='manager', admin=True)
    assert '캐릭터를 골라' in bot('사용/축복 부적')
    text = bot('사용/축복 부적/@Sophia')
    assert text.startswith('@Sophia ') and 'Sophia에게 축복 부적을 사용했습니다' in text
    assert '축복 부적' not in character(bot, 'Elena')['items']
    assert ('POST', 'items/use') in bot.server.calls


def test_discard_does_not_change_stats(bot):
    assert '버렸습니다' in bot('버리기/반지')
    assert character(bot, 'Elena')['stats']['정신력'] == 30


def test_transfer_money_and_items(bot):
    assert '소피아에게 5코인을 양도했습니다' in bot('양도/5코인/소피아')
    assert character(bot, 'Sophia')['money'] == 6
    assert '소피아에게 반지 1개를 양도했습니다' in bot('양도/반지/소피아')
    assert character(bot, 'Sophia')['items']['반지'] == 1
    assert '자기 자신' in bot('양도/1/엘레나')
    assert '소지금이 부족합니다' in bot('양도/100/소피아')


def test_attendance_once_per_day(bot):
    assert '출석 완료' in bot('출석')
    assert '이미 출석' in bot('출석')
    assert character(bot, 'Elena')['money'] == 13


def test_randombox(bot):
    listing = bot('랜덤박스')
    assert '행운 상자 (5코인)' in listing
    text = bot('랜덤박스/행운 상자')
    assert '열었습니다' in text
    assert character(bot, 'Elena')['money'] == 7
    assert '보물 상자가 없습니다' in bot('랜덤박스/보물 상자')


def test_admin_commands(bot):
    assert '운영진만' in bot('소지금 추가/10/겐지')
    text = bot('소지금 추가/10/엘레나, 소피아', user='manager', admin=True)
    assert '엘레나: +10 → 22코인' in text
    assert character(bot, 'Sophia')['money'] == 11
    text = bot('체력 변경/-10/전원', user='manager', admin=True)
    assert '마야: 체력 91 → 81' in text
    assert "'용기' 스탯이 없습니다" in bot('용기 변경/1/전원', user='manager', admin=True)
    assert '상태 수치라 바꿀 수 없습니다' in bot('공격력 변경/1/전원', user='manager', admin=True)
    bot('아이템 추가/사과: 2, 포도: 3/루카스', user='manager', admin=True)
    assert character(bot, 'Lucas')['items'] == {'사과': 2, '포도': 3}
    bot('아이템 차감/사과: 5/루카스', user='manager', admin=True)
    assert character(bot, 'Lucas')['items'] == {'포도': 3}
    assert '찾지 못한 캐릭터: 없는사람' in bot('소지금 차감/1/엘레나, 없는사람', user='manager', admin=True)
    assert '루카스: 소지금이 부족합니다' in bot('소지금 차감/10/루카스', user='manager', admin=True)
    assert all(record['kind'] == 'admin' for record in bot.server.records)


def test_not_registered(bot):
    assert '등록된 캐릭터가 아닙니다' in bot('가방', user='stranger')


def test_renamed_commands_from_server(bot):
    """컨텐츠 설정 > 상점에서 바꾼 이름으로 받고, 바뀐 예전 이름은 받지 않는다."""
    bot.server.config['commands'] = {'buy': '사기', 'bag': '내 가방', 'help': '명령어', 'stat_change': '조정'}
    before = character(bot, 'Elena')['money']
    assert '구매했습니다' in bot('사기/동화책')
    assert character(bot, 'Elena')['money'] == before - 1
    assert '엘레나의 가방' in bot('내가방')
    assert '없는 명령어입니다. [명령어]를 확인해 주세요.' in bot('구매/동화책')
    assert bot('명령어').startswith('명령어')
    text = bot('체력 조정/-1/마야', user='manager', admin=True)
    assert '마야: 체력 91 → 90' in text
    assert '없는 명령어' in bot('체력 변경/-1/마야', user='manager', admin=True)


def test_admins_from_server(bot):
    """서버의 운영진 계정도 운영진 명령어를 쓸 수 있다 (자동봇 설정의 운영진과 함께)."""
    assert '운영진만' in bot('소지금 추가/1/마야', user='Grace')
    bot.server.config['admins'] = ['Grace']
    assert '마야: +1' in bot('소지금 추가/1/마야', user='Grace')
    assert '마야: +1' in bot('소지금 추가/1/마야', user='manager')


def test_closed_shop_refuses_list_and_purchase(bot, server):
    server.config['shop_open'] = False
    assert bot('상점') == '지금은 상점이 열려 있지 않습니다.'
    assert bot('구매/사과') == '지금은 상점이 열려 있지 않습니다.'
    assert '[가방]' not in bot('가방')


def test_transfer_finds_the_character_by_the_first_part_of_the_name(bot):
    """'소피아' 를 '소피' 만 써도 찾는다 (하나로 정해질 때만, 여럿이면 다시 물어본다)."""
    assert '소피아에게 5코인을 양도했습니다' in bot('양도/5코인/소피')
    assert character(bot, 'Sophia')['money'] == 6
    assert '찾을 수 없습니다' in bot('양도/1/없는이름')
