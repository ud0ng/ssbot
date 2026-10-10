"""자동봇 실행: python main.py"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from ssbot.commands import Processor, Request, first_command
from ssbot.config import Config, now_kst
from ssbot.mastodon_io import MAIN, Hub, html_to_text, listen, missed_mentions, reply_visibility
from ssbot.reservation import ReservationScheduler
from ssbot.rewards import TootRewards
from ssbot.sheets import CsvBackend, GoogleBackend, Sheets
from ssbot.state import State
from ssbot.story import StoryRunner
from ssbot.text import normalize_key

logger = logging.getLogger('ssbot')


def setup_logging() -> None:
    Path('logs').mkdir(exist_ok=True)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s', '%m-%d %H:%M:%S')
    handlers = [logging.StreamHandler(sys.stdout), RotatingFileHandler('logs/bot.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')]
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    logging.getLogger('urllib3').setLevel(logging.WARNING)


class App:
    def __init__(self, config: Config):
        self.config = config
        self.state = State(config.state_path)

        local_dir = os.getenv('LOCAL_SHEETS_DIR')
        backend = CsvBackend(local_dir) if local_dir else GoogleBackend(config.credentials_path, config.sheet_ids)
        logger.info('구글 시트 인증: %s', getattr(backend, 'identity', '로컬 CSV'))
        self.sheets = Sheets(backend, ttl=config.cache_seconds)

        tokens = {MAIN: config.bot_token, **config.accounts}
        self.hub = Hub(config.api_base_url, tokens, config.max_length)
        self.processor = Processor(config, self.sheets, self.state)
        self.story = StoryRunner(self.sheets, self._post_as, config.story_visibility)
        self.reservations = ReservationScheduler(self.sheets, self.state, self._post_as,
                                                 config.reservation_catch_up_minutes, config.public_visibility)
        self.rewards = TootRewards(self.sheets, self.state, self.processor.ledger, config.toot_reward_every, config.toot_reward_amount)
        self._handled = deque(maxlen=500)
        self._handled_lock = threading.Lock()

    def _post_as(self, account: str, text: str, visibility: str):
        return self.hub.post(account, text, visibility)

    # ------------------------------------------------------------ 멘션

    def on_mention(self, listener: str, notification) -> None:
        with self._handled_lock:
            key = (listener, str(notification['id']))
            if key in self._handled:
                return
            self._handled.append(key)
        self._remember(listener, notification['id'])

        status = notification['status']
        sender = notification['account']
        command = first_command(html_to_text(status['content']))
        if not command:
            return

        sender_acct = sender['acct']
        from_own_account = str(sender['id']) in self.hub.own_ids
        is_story_command = normalize_key(command).startswith('스진')

        try:
            if is_story_command and listener == self._story_listener():
                self._handle_story(status, sender_acct, command, from_own_account)
                return
            if listener != MAIN or from_own_account:
                return

            logger.info('명령어 @%s [%s]', sender_acct, command)
            reply = self.processor.handle(Request(
                user=sender_acct,
                command=command,
                display_name=sender.get('display_name') or sender['username'],
                is_admin=self.config.is_admin(sender_acct),
            ))
            if reply is not None:
                self.hub.post(MAIN, reply.text, reply_visibility(status['visibility']), status['id'],
                              prefix=self._mentions(status, sender_acct), media_urls=reply.media_urls)
        except Exception:
            logger.exception('명령어 처리 실패: @%s [%s]', sender_acct, command)
            try:
                self.hub.post(MAIN, '명령어를 처리하는 중 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.',
                              reply_visibility(status['visibility']), status['id'], prefix=f'@{sender_acct} ')
            except Exception:
                logger.exception('오류 안내 실패')

    def _mentions(self, status, sender_acct: str) -> str:
        names = [sender_acct]
        for mention in status.get('mentions') or []:
            acct = mention['acct']
            if acct.lower() not in self.hub.own_accts and acct not in names:
                names.append(acct)
        return ' '.join(f'@{name}' for name in names) + ' '

    def _story_listener(self) -> str:
        return self.config.story_account if self.config.story_account in self.hub.clients else MAIN

    def _handle_story(self, status, sender_acct: str, command: str, from_own_account: bool) -> None:
        if not (from_own_account or self.config.is_admin(sender_acct)):
            message = '스토리 진행은 운영진만 할 수 있습니다.'
        else:
            parts = [part.strip() for part in command.split('/')]
            if normalize_key(parts[0]) in ('스진중지', '스진멈춤') or (len(parts) > 1 and normalize_key(parts[1]) in ('중지', '멈춤')):
                message = self.story.stop()
            elif len(parts) < 2 or not parts[1]:
                message = '형식: [스진/워크시트명]'
            else:
                message = self.story.start(parts[1])
        logger.info('스토리 명령 @%s [%s] → %s', sender_acct, command, message)
        if not from_own_account:
            self.hub.post(self._story_listener(), message, 'direct', status['id'], prefix=f'@{sender_acct} ')

    def _remember(self, listener: str, notification_id) -> None:
        current = self.state.get('last_notification', listener)
        if current is None or int(notification_id) > int(current):
            self.state.set('last_notification', listener, str(notification_id))

    # ------------------------------------------------------------ 반복 작업

    def _loop(self, name: str, interval: float, job) -> None:
        def run():
            while True:
                try:
                    job()
                except Exception:
                    logger.exception('%s 작업 실패', name)
                time.sleep(interval)
        threading.Thread(target=run, name=name, daemon=True).start()

    def run(self) -> None:
        listeners = [MAIN]
        if self._story_listener() != MAIN:
            listeners.append(self._story_listener())

        for name in listeners:
            for notification in missed_mentions(self.hub, name, self.state.get('last_notification', name)):
                self.on_mention(name, notification)
            listen(self.hub, name, self.on_mention)
            logger.info('멘션 수신 시작: %s', name)

        if self.sheets.has('reservation'):
            self._loop('reservation', 20, lambda: self.reservations.tick(now_kst()))
            logger.info('예약 툿 확인 시작 (20초마다)')
        if self.config.toot_reward_every > 0:
            self._loop('toot-rewards', self.config.toot_reward_interval_minutes * 60,
                       lambda: self.rewards.run_once(self.hub.toot_count))
            logger.info('툿 수 보상 확인 시작 (%d분마다)', self.config.toot_reward_interval_minutes)

        logger.info('자동봇이 시작되었습니다.')
        while True:
            time.sleep(3600)


def main() -> None:
    load_dotenv()
    setup_logging()
    config = Config.from_env()
    missing = [name for name, value in (('MASTODON_API_BASE_URL', config.api_base_url),
                                        ('MASTODON_ACCESS_TOKEN', config.bot_token)) if not value]
    if missing:
        logger.error('.env 에 %s 를 채워 주세요.', ', '.join(missing))
        sys.exit(1)
    if not any(config.sheet_ids.values()) and not os.getenv('LOCAL_SHEETS_DIR'):
        logger.error('.env 에 시트 ID 를 하나 이상 채워 주세요 (MAIN_SHEET_ID 등).')
        sys.exit(1)
    App(config).run()


if __name__ == '__main__':
    main()
