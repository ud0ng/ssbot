"""마스토돈 연결: 여러 계정, 툿 올리기, 멘션 수신."""

from __future__ import annotations

import logging
import mimetypes
import re
import threading
from typing import Callable, Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from mastodon import Mastodon, StreamListener

logger = logging.getLogger(__name__)

MAIN = 'BOT'


def html_to_text(html: str) -> str:
    html = re.sub(r'<br\s*/?>', '\n', html or '')
    html = re.sub(r'</p>\s*<p>', '\n\n', html)
    return BeautifulSoup(html, 'html.parser').get_text()


def reply_visibility(visibility: str) -> str:
    """요청 툿의 공개 범위에 맞춰 답장한다. DM → DM, 조용한 공개 → 조용한 공개, 그 외 → 팔로워."""
    return {'direct': 'direct', 'unlisted': 'unlisted'}.get(visibility, 'private')


def chunks(text: str, limit: int) -> List[str]:
    """글자수 제한에 맞춰 줄 단위로 나눈다."""
    if len(text) <= limit:
        return [text]
    parts, current = [], ''
    for line in text.split('\n'):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ''
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f'{current}\n{line}' if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


class Hub:
    """계정 이름(BOT, NOTICE, STORY ...) → Mastodon 클라이언트."""

    def __init__(self, api_base_url: str, tokens: Dict[str, str], max_length: int = 1000):
        self.max_length = max_length
        self.clients: Dict[str, Mastodon] = {}
        self.me: Dict[str, dict] = {}
        for name, token in tokens.items():
            client = Mastodon(access_token=token, api_base_url=api_base_url, request_timeout=30)
            self.me[name] = client.account_verify_credentials()
            self.clients[name] = client
            logger.info('계정 연결: %s = @%s', name, self.me[name]['acct'])
        self._media_cache: Dict[str, tuple] = {}

    @property
    def own_ids(self) -> set:
        return {str(account['id']) for account in self.me.values()}

    @property
    def own_accts(self) -> set:
        return {account['acct'].lower() for account in self.me.values()}

    def client(self, name: str) -> Mastodon:
        if name not in self.clients:
            raise KeyError(f"'{name}' 계정 토큰이 설정되지 않았습니다 (.env 의 ACCOUNT_{name}).")
        return self.clients[name]

    def _upload(self, client: Mastodon, url: str) -> Optional[str]:
        try:
            if url not in self._media_cache:
                response = requests.get(url, timeout=20)
                response.raise_for_status()
                mime = response.headers.get('content-type', '').split(';')[0] or mimetypes.guess_type(url)[0] or 'image/png'
                self._media_cache[url] = (response.content, mime)
            content, mime = self._media_cache[url]
            return client.media_post(content, mime_type=mime, file_name='image' + (mimetypes.guess_extension(mime) or '.png'))['id']
        except Exception:
            logger.exception('이미지 업로드 실패: %s', url)
            return None

    def post(self, account: str, text: str, visibility: str = 'public', in_reply_to_id=None,
             prefix: str = '', media_urls: Optional[List[str]] = None):
        """text 를 글자수에 맞게 나눠 이어서(스레드로) 올린다. prefix(멘션)는 매 조각 앞에 붙인다."""
        client = self.client(account)
        media_ids = [media for media in (self._upload(client, url) for url in (media_urls or [])) if media]
        last = None
        for index, part in enumerate(chunks(text, max(self.max_length - len(prefix), 100))):
            last = client.status_post(
                prefix + part,
                in_reply_to_id=last['id'] if last else in_reply_to_id,
                visibility=visibility,
                media_ids=media_ids if index == 0 and media_ids else None,
            )
        return last

    def toot_count(self, acct: str) -> Optional[int]:
        try:
            return int(self.client(MAIN).account_lookup(acct)['statuses_count'])
        except Exception:
            logger.warning('툿 수 확인 실패: %s', acct)
            return None


class MentionListener(StreamListener):
    def __init__(self, name: str, handler: Callable[[str, dict], None]):
        super().__init__()
        self.name = name
        self.handler = handler

    def on_notification(self, notification):
        if notification.get('type') == 'mention' and notification.get('status'):
            threading.Thread(target=self.handler, args=(self.name, notification), daemon=True).start()

    def on_abort(self, err):
        logger.warning('%s 스트림 끊김: %s (자동 재연결)', self.name, err)


def listen(hub: Hub, name: str, handler: Callable[[str, dict], None]):
    """스트리밍으로 멘션을 받는다 (끊기면 자동 재연결)."""
    return hub.client(name).stream_user(MentionListener(name, handler), run_async=True,
                                        reconnect_async=True, reconnect_async_wait_sec=5)


def missed_mentions(hub: Hub, name: str, since_id: Optional[str]) -> list:
    """봇이 꺼져 있던 사이 받은 멘션 (오래된 순)."""
    if not since_id:
        return []
    try:
        notifications = hub.client(name).notifications(since_id=since_id, types=['mention'], limit=40)
    except Exception:
        logger.exception('놓친 멘션 확인 실패: %s', name)
        return []
    return list(reversed(notifications))
