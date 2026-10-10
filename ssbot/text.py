"""문구 처리: 주사위, 한국어 조사, 키워드 답변 치환.

치환 문법 (키워드 답변 · 진입/조사 문구 · 스토리 · 예약 툿 공통)
  {시전자}            명령어를 보낸 사람 이름 (명단/관리 시트의 이름, 없으면 표시 이름)
  {1d100} {3d6+5}     주사위 결과
  {랜덤: A, B, C}     적어 둔 것 중 하나
  {은는} {이가} {을를} {과와} {와과} {아야} {으로로} {로으로}
                      바로 앞 글자의 받침에 맞는 조사
"""

from __future__ import annotations

import random
import re
from typing import Optional

MAX_DICE_COUNT = 100
MAX_DICE_SIDES = 1000

_DICE = re.compile(r'^\s*(-)?\(?\s*(\d+)\s*[dD]\s*(\d+)\s*(?:([+-])\s*(\d+))?\s*\)?\s*$')
_INT = re.compile(r'^\s*([+-]?\d+)\s*$')


class DiceError(ValueError):
    pass


def is_dice(expr: str) -> bool:
    return bool(_DICE.match(expr or ''))


def roll(expr: str, rng: Optional[random.Random] = None) -> int:
    """`3d6`, `1d100+5`, `2d6-1`, `-(1d6+3)` 을 굴려 합계를 돌려준다."""
    match = _DICE.match(expr or '')
    if not match:
        raise DiceError(f'주사위 표현식이 아닙니다: {expr}')

    negative, count, sides, sign, modifier = match.groups()
    count, sides = int(count), int(sides)
    if not 1 <= count <= MAX_DICE_COUNT:
        raise DiceError(f'주사위 개수는 1~{MAX_DICE_COUNT}개까지 굴릴 수 있습니다.')
    if not 2 <= sides <= MAX_DICE_SIDES:
        raise DiceError(f'주사위 면 수는 2~{MAX_DICE_SIDES}까지 가능합니다.')

    rng = rng or random
    total = sum(rng.randint(1, sides) for _ in range(count))
    if modifier:
        total += int(modifier) if sign == '+' else -int(modifier)
    return -total if negative else total


def amount(expr: str, rng: Optional[random.Random] = None) -> int:
    """정수(`+3`, `-10`) 또는 주사위 표현식(`1d10`, `+1d3`, `-(1d6+3)`)을 값으로 바꾼다."""
    text = (expr or '').strip()
    int_match = _INT.match(text)
    if int_match:
        return int(int_match.group(1))

    sign = 1
    if text.startswith('+'):
        text = text[1:]
    elif text.startswith('-') and not text.startswith('-('):
        sign, text = -1, text[1:]
    return sign * roll(text, rng)


# ---------------------------------------------------------------- 조사

_DIGIT_FINAL = {'0': 'ㅇ', '1': 'ㄹ', '2': '', '3': 'ㅁ', '4': '', '5': '', '6': 'ㄱ', '7': 'ㄹ', '8': 'ㄹ', '9': ''}
_FINALS = ['', 'ㄱ', 'ㄲ', 'ㄳ', 'ㄴ', 'ㄵ', 'ㄶ', 'ㄷ', 'ㄹ', 'ㄺ', 'ㄻ', 'ㄼ', 'ㄽ', 'ㄾ', 'ㄿ', 'ㅀ',
           'ㅁ', 'ㅂ', 'ㅄ', 'ㅅ', 'ㅆ', 'ㅇ', 'ㅈ', 'ㅊ', 'ㅋ', 'ㅌ', 'ㅍ', 'ㅎ']
_SKIP_BEFORE_JOSA = set(')]}>"\'”’」』》〉 ')

# 태그 → (받침 있을 때, 받침 없을 때)
_JOSA = {
    '은는': ('은', '는'), '이가': ('이', '가'), '을를': ('을', '를'),
    '과와': ('과', '와'), '와과': ('과', '와'), '아야': ('아', '야'),
    '이여': ('이여', '여'), '이라': ('이라', '라'),
}
_JOSA_RO = {'으로로', '로으로'}
_JOSA_TAG = re.compile(r'\{(' + '|'.join(list(_JOSA) + list(_JOSA_RO)) + r')\}')


def final_consonant(text: str) -> str:
    """마지막 글자의 받침 ('' = 받침 없음)."""
    for char in reversed(text or ''):
        if char in _SKIP_BEFORE_JOSA:
            continue
        if '가' <= char <= '힣':
            return _FINALS[(ord(char) - ord('가')) % 28]
        if char.isdigit():
            return _DIGIT_FINAL[char]
        if char.isalpha():
            return 'ㄹ' if char.lower() == 'l' else ('ㄴ' if char.lower() in 'mn' else '')
        return ''
    return ''


def josa(word: str, with_final: str, without_final: str) -> str:
    return with_final if final_consonant(word) else without_final


def apply_josa(text: str) -> str:
    """`{은는}` 같은 조사 태그를 바로 앞 글자에 맞춰 바꾼다."""
    result: list[str] = []
    last = 0
    for match in _JOSA_TAG.finditer(text):
        result.append(text[last:match.start()])
        preceding = ''.join(result)
        tag = match.group(1)
        final = final_consonant(preceding)
        if tag in _JOSA_RO:
            result.append('으로' if final and final != 'ㄹ' else '로')
        else:
            with_final, without_final = _JOSA[tag]
            result.append(with_final if final else without_final)
        last = match.end()
    result.append(text[last:])
    return ''.join(result)


# ---------------------------------------------------------------- 치환

_CASTER = re.compile(r'\{\s*시전자\s*\}')
_RANDOM = re.compile(r'\{\s*랜덤\s*[:：]\s*([^{}]*)\}')
_DICE_TAG = re.compile(r'\{\s*([+-]?\(?\s*\d+\s*[dD]\s*\d+\s*(?:[+-]\s*\d+)?\s*\)?)\s*\}')


def split_options(text: str) -> list[str]:
    return [option.strip() for option in re.split(r'[,，]', text or '') if option.strip()]


def render(text: str, caster: str = '', rng: Optional[random.Random] = None) -> str:
    """시트 문구의 치환 태그를 채운다. 모르는 중괄호는 그대로 둔다."""
    if not text:
        return ''
    rng = rng or random
    text = _CASTER.sub(lambda _: caster, text)
    text = _RANDOM.sub(lambda m: rng.choice(split_options(m.group(1)) or ['']), text)

    def dice(match: re.Match) -> str:
        try:
            return str(amount(match.group(1).replace(' ', ''), rng))
        except DiceError:
            return match.group(0)

    text = _DICE_TAG.sub(dice, text)
    return apply_josa(text)


def normalize_key(text: str) -> str:
    """키워드·이름 비교용: 공백 제거, 소문자."""
    return re.sub(r'\s+', '', text or '').lower()
