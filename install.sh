#!/usr/bin/env bash
# 자동봇 설치 (Ubuntu / Debian)
#
#   curl -fsSL https://raw.githubusercontent.com/ud0ng/ssbot/main/install.sh | bash
#
# 1. 필요한 패키지 설치 → 2. ~/ssbot 에 내려받기 → 3. 파이썬 가상환경
# 4. 설정(.env) 질문 → 5. 서버가 켜질 때 자동 실행(systemd) 등록
# 다시 실행하면 코드를 최신으로 받고 설정은 그대로 둔다.

set -euo pipefail

REPO="${SSBOT_REPO:-https://github.com/ud0ng/ssbot.git}"
DIR="${SSBOT_DIR:-$HOME/ssbot}"
SERVICE=ssbot
TTY=/dev/tty

ask() {   # ask 변수 "질문" [기본값]
  local var="$1" label="$2" default="${3:-}" value=""
  if [[ -n "$default" ]]; then label="$label [$default]"; fi
  read -r -p "$label: " value < "$TTY" || true
  printf -v "$var" '%s' "${value:-$default}"
}

echo '== 1/5 패키지 설치 =='
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3 python3-venv python3-pip nano >/dev/null

echo '== 2/5 내려받기 =='
if [[ -d "$DIR/.git" ]]; then
  git -C "$DIR" pull --ff-only
else
  git clone "$REPO" "$DIR"
fi
cd "$DIR"

echo '== 3/5 파이썬 가상환경 =='
python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt

echo '== 4/5 설정 =='
if [[ -f .env ]]; then
  echo '.env 가 이미 있어서 그대로 둡니다. (고치려면: nano ~/ssbot/.env)'
else
  echo '모르는 항목은 엔터로 넘기면 됩니다. 나중에 nano ~/ssbot/.env 로 고칠 수 있습니다.'
  echo
  ask SERVER '마스토돈 서버 주소 (https:// 빼고)'
  SERVER="${SERVER#https://}"; SERVER="${SERVER%/}"
  ask BOT_TOKEN '메인 봇 계정 액세스 토큰'
  ask NOTICE_TOKEN 'NOTICE(공지) 계정 토큰 — 공지 계정 manager 의 토큰 권장'
  ask STORY_TOKEN 'STORY 계정 토큰'
  ask SYSTEM_TOKEN 'SYSTEM 계정 토큰'
  ask ADMINS '운영진 아이디 (@ 없이, 쉼표로 여러 명)' 'manager'
  ask LENGTH '툿 최대 글자수' '1000'
  ask MAIN_ID '기본(상점/스탯) 시트 ID'
  ask RESERVATION_ID '예약 툿 시트 ID'
  ask STORY_ID '스토리 시트 ID'
  ask INVESTIGATION_ID '조사 시트 ID'
  ask CURRENCY '재화 이름' '코인'
  ask ATTENDANCE '출석 보상 (0 = 출석 끔)' '0'
  ask TOOT_EVERY '툿 몇 개마다 재화를 줄까요 (0 = 끔)' '0'
  ask TOOT_AMOUNT '그때 줄 재화' '1'
  ask LIMIT '하루 조사 횟수 제한 (0 = 제한 없음)' '0'

  umask 077
  cat > .env <<EOF
MASTODON_API_BASE_URL=https://${SERVER}
MASTODON_ACCESS_TOKEN=${BOT_TOKEN}
ACCOUNT_NOTICE=${NOTICE_TOKEN}
ACCOUNT_STORY=${STORY_TOKEN}
ACCOUNT_SYSTEM=${SYSTEM_TOKEN}
STORY_ACCOUNT=STORY
ADMIN_IDS=${ADMINS}
MAX_MESSAGE_LENGTH=${LENGTH}

GOOGLE_CREDENTIALS_PATH=credentials.json
MAIN_SHEET_ID=${MAIN_ID}
RESERVATION_SHEET_ID=${RESERVATION_ID}
STORY_SHEET_ID=${STORY_ID}
INVESTIGATION_SHEET_ID=${INVESTIGATION_ID}
CACHE_SECONDS=60

CURRENCY_NAME=${CURRENCY}
TRANSFER_ENABLED=true
ATTENDANCE_REWARD=${ATTENDANCE}
TOOT_REWARD_EVERY=${TOOT_EVERY}
TOOT_REWARD_AMOUNT=${TOOT_AMOUNT}
TOOT_REWARD_INTERVAL_MINUTES=60
INVESTIGATION_DAILY_LIMIT=${LIMIT}
RANDOMBOX_COMMAND=랜덤박스
KEYWORD_IMAGES=true
PUBLIC_VISIBILITY=unlisted
STORY_VISIBILITY=unlisted
EOF
  umask 022
  echo '.env 를 저장했습니다.'
fi

echo '== 5/5 자동 실행 등록 =='
sudo tee /etc/systemd/system/${SERVICE}.service >/dev/null <<EOF
[Unit]
Description=Mastodon 자동봇
After=network-online.target
Wants=network-online.target

[Service]
User=$(id -un)
WorkingDirectory=${DIR}
ExecStart=${DIR}/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE} >/dev/null 2>&1

META=http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default
if [[ -f credentials.json ]]; then
  echo 'credentials.json(서비스 계정 키)으로 구글 시트에 접속합니다.'
elif SA_EMAIL=$(curl -fs -H 'Metadata-Flavor: Google' "$META/email" 2>/dev/null); then
  SA_SCOPES=$(curl -fs -H 'Metadata-Flavor: Google' "$META/scopes" 2>/dev/null || true)
  echo
  echo "이 서버(GCP VM)의 서비스 계정으로 구글 시트에 접속합니다: ${SA_EMAIL}"
  echo '→ 이 이메일을 시트마다 [공유]에 편집자로 추가하세요.'
  if ! grep -q 'auth/spreadsheets' <<< "$SA_SCOPES"; then
    cat <<'WARN'

⚠ 이 VM 에 아직 구글 시트 권한(auth/spreadsheets)이 없습니다. README 의 'VM 에 시트 권한 주기'를 따라
  Cloud Shell 에서 VM 을 잠시 끄고 권한을 추가한 뒤 다시 켜 주세요.
WARN
  fi
else
  echo '구글 시트 인증 정보가 없습니다. credentials.json 을 넣거나 GCP VM 에서 실행하세요.'
fi

sudo systemctl restart "${SERVICE}"
echo
echo '자동봇을 시작했습니다. 상태 확인: sudo systemctl status ssbot   로그: tail -f ~/ssbot/logs/bot.log'
