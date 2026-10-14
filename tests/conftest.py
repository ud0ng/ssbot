import random
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from fake_server import FakeServer  # noqa: E402
from ssbot.commands import Processor, Request  # noqa: E402
from ssbot.config import KST, Config  # noqa: E402
from ssbot.server import ServerLedger  # noqa: E402
from ssbot.sheets import CsvBackend, Sheets  # noqa: E402
from ssbot.state import State  # noqa: E402

FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def sheet_dir(tmp_path):
    target = tmp_path / 'sheets'
    shutil.copytree(FIXTURES, target)
    return target


@pytest.fixture
def config():
    return Config(admins=['manager'])


@pytest.fixture
def clock():
    return lambda: datetime(2026, 10, 11, 12, 0, tzinfo=KST)


@pytest.fixture
def server():
    """마스토돈 /api/v1/bot 흉내 (캐릭터·상점·재화 규칙)."""
    return FakeServer()


@pytest.fixture
def ledger(server):
    return ServerLedger(server, cache_seconds=0)


@pytest.fixture
def bot(sheet_dir, tmp_path, config, clock, ledger, server):
    sheets = Sheets(CsvBackend(sheet_dir), ttl=0)
    processor = Processor(config, sheets, State(tmp_path / 'state.json'), ledger, rng=random.Random(1), clock=clock)

    def send(command, user='Elena', admin=False, display='엘레나 표시'):
        reply = processor.handle(Request(user=user, command=command, display_name=display, is_admin=admin))
        return reply.text if reply else None

    send.processor = processor
    send.sheets = sheets
    send.server = server
    return send
