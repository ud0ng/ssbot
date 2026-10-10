"""설정 (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> str:
    return now_kst().strftime('%Y-%m-%d')


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == '':
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'y', 'on')


def _int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or '').strip())
    except ValueError:
        return default


def _list(name: str) -> List[str]:
    return [part.strip().lstrip('@') for part in (os.getenv(name) or '').split(',') if part.strip()]


@dataclass
class Config:
    # 마스토돈
    api_base_url: str = ''
    bot_token: str = ''                       # 명령어를 받는 메인 봇 계정
    accounts: Dict[str, str] = field(default_factory=dict)   # 예약 툿·스토리용 계정 이름 → 토큰
    story_account: str = 'STORY'              # [스진] DM 을 받는 계정 이름
    max_length: int = 1000
    admins: List[str] = field(default_factory=list)          # 운영진 아이디

    # 구글 시트
    credentials_path: str = 'credentials.json'
    sheet_ids: Dict[str, str] = field(default_factory=dict)  # main / reservation / story / investigation
    cache_seconds: int = 60

    # 기능
    currency: str = '코인'
    transfer_enabled: bool = True
    attendance_reward: int = 0                # 0 이면 출석 기능 끔
    toot_reward_every: int = 0                # 툿 N개마다 (0 이면 끔)
    toot_reward_amount: int = 1
    toot_reward_interval_minutes: int = 60
    investigation_daily_limit: int = 0        # 0 이면 제한 없음
    randombox_command: str = '랜덤박스'
    keyword_images: bool = True
    story_visibility: str = 'unlisted'
    # 시트의 '일반(공개)' 범위를 실제로 무엇으로 올릴지. 이 서버는 공개 툿을 막고
    # 공지 계정의 조용한 공개만 허용하므로 기본값은 unlisted.
    public_visibility: str = 'unlisted'
    reservation_catch_up_minutes: int = 10    # 봇이 꺼져 있던 사이 놓친 예약 툿을 몇 분 전 것까지 보낼지

    state_path: str = 'data/state.json'

    @classmethod
    def from_env(cls) -> 'Config':
        accounts = {}
        for key, value in os.environ.items():
            if key.startswith('ACCOUNT_') and value.strip():
                accounts[key[len('ACCOUNT_'):].upper()] = value.strip()

        url = (os.getenv('MASTODON_API_BASE_URL') or '').strip().rstrip('/')
        if url and not url.startswith('http'):
            url = f'https://{url}'

        return cls(
            api_base_url=url,
            bot_token=(os.getenv('MASTODON_ACCESS_TOKEN') or '').strip(),
            accounts=accounts,
            story_account=(os.getenv('STORY_ACCOUNT') or 'STORY').strip().upper(),
            max_length=_int('MAX_MESSAGE_LENGTH', 1000),
            admins=_list('ADMIN_IDS'),
            credentials_path=(os.getenv('GOOGLE_CREDENTIALS_PATH') or 'credentials.json').strip(),
            sheet_ids={
                'main': (os.getenv('MAIN_SHEET_ID') or '').strip(),
                'reservation': (os.getenv('RESERVATION_SHEET_ID') or '').strip(),
                'story': (os.getenv('STORY_SHEET_ID') or '').strip(),
                'investigation': (os.getenv('INVESTIGATION_SHEET_ID') or '').strip(),
            },
            cache_seconds=_int('CACHE_SECONDS', 60),
            currency=(os.getenv('CURRENCY_NAME') or '코인').strip(),
            transfer_enabled=_bool('TRANSFER_ENABLED', True),
            attendance_reward=_int('ATTENDANCE_REWARD', 0),
            toot_reward_every=_int('TOOT_REWARD_EVERY', 0),
            toot_reward_amount=_int('TOOT_REWARD_AMOUNT', 1),
            toot_reward_interval_minutes=_int('TOOT_REWARD_INTERVAL_MINUTES', 60),
            investigation_daily_limit=_int('INVESTIGATION_DAILY_LIMIT', 0),
            randombox_command=(os.getenv('RANDOMBOX_COMMAND') or '랜덤박스').strip(),
            keyword_images=_bool('KEYWORD_IMAGES', True),
            story_visibility=(os.getenv('STORY_VISIBILITY') or 'unlisted').strip(),
            public_visibility=(os.getenv('PUBLIC_VISIBILITY') or 'unlisted').strip(),
            reservation_catch_up_minutes=_int('RESERVATION_CATCH_UP_MINUTES', 10),
            state_path=(os.getenv('STATE_PATH') or 'data/state.json').strip(),
        )

    def is_admin(self, acct: str) -> bool:
        local = (acct or '').lstrip('@').split('@')[0].lower()
        return any(local == admin.split('@')[0].lower() for admin in self.admins)
