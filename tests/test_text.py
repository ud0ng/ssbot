import random

import pytest

from ssbot.text import DiceError, amount, apply_josa, final_consonant, render, roll


def test_roll_range_and_modifier():
    rng = random.Random(0)
    for _ in range(200):
        assert 1 <= roll('1d6', rng) <= 6
        assert 6 <= roll('1d6+5', rng) <= 11
        assert -9 <= roll('-(1d6+3)', rng) <= -4


def test_roll_limits():
    with pytest.raises(DiceError):
        roll('101d6')
    with pytest.raises(DiceError):
        roll('1d1')


def test_amount_accepts_ints_and_signed_dice():
    rng = random.Random(0)
    assert amount('+3') == 3
    assert amount('-20') == -20
    assert 1 <= amount('+1d3', rng) <= 3
    assert -6 <= amount('-1d6', rng) <= -1


@pytest.mark.parametrize('word,expected', [('제이', ''), ('겐지', ''), ('광어', ''), ('송어', ''), ('철수', ''),
                                           ('곰', 'ㅁ'), ('3', 'ㅁ'), ('10', 'ㅇ'), ('물', 'ㄹ'), ('[모래밭]', 'ㅌ')])
def test_final_consonant(word, expected):
    assert final_consonant(word) == expected


def test_apply_josa():
    assert apply_josa('제이{은는} 밥') == '제이는 밥'
    assert apply_josa('곰{이가} 왔다') == '곰이 왔다'
    assert apply_josa('물{으로로} 씻다') == '물로 씻다'
    assert apply_josa('집{으로로} 가다') == '집으로 가다'
    assert apply_josa('바다{으로로} 가다') == '바다로 가다'
    assert apply_josa('광어{을를} 낚았다') == '광어를 낚았다'


def test_render_full_example():
    rng = random.Random(3)
    text = render('{시전자}{이가} 스킬을 사용합니다. {랜덤: 물리, 마법} 피해 {5d5+5}!', '제이', rng)
    assert text.startswith('제이가 스킬을 사용합니다. ')
    assert ('물리' in text) or ('마법' in text)
    damage = int(text.split('피해 ')[1].rstrip('!'))
    assert 10 <= damage <= 30


def test_render_random_then_josa():
    rng = random.Random(0)
    for _ in range(20):
        text = render('펄떡이는 {랜덤: 송어, 광어, 돌돔, 참돔}{을를} 낚았다!', '', rng)
        fish = text.split(' ')[1][:2]
        assert text.endswith(('을 낚았다!', '를 낚았다!'))
        assert text == f"펄떡이는 {fish}{'을' if fish in ('돌돔', '참돔') else '를'} 낚았다!"


def test_render_leaves_unknown_braces():
    assert render('{모르는것} 그대로') == '{모르는것} 그대로'
