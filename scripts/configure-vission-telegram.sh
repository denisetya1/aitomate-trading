#!/usr/bin/env bash
set -euo pipefail

profile_dir="${HOME}/.hermes/profiles/vission"
env_file="${profile_dir}/.env"
source_env="${HOME}/.hermes/profiles/edith/.env"

mkdir -p "${profile_dir}"
touch "${env_file}"
chmod 600 "${env_file}"

printf 'Paste token bot Telegram Vission (input disembunyikan): '
IFS= read -r -s bot_token
printf '\n'

if [[ ! "${bot_token}" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
  printf 'Format token tidak valid. Tidak ada perubahan.\n' >&2
  exit 2
fi

for other_env in "${HOME}"/.hermes/profiles/*/.env; do
  [[ "${other_env}" == "${env_file}" ]] && continue
  existing_token="$(sed -n 's/^TELEGRAM_BOT_TOKEN=//p' "${other_env}" | tail -n 1)"
  if [[ -n "${existing_token}" && "${existing_token}" == "${bot_token}" ]]; then
    printf 'Token ini sudah dipakai profil lain. Gunakan bot khusus Vission.\n' >&2
    exit 3
  fi
done

bot_name="$(printf '%s' "${bot_token}" | python3 -c '
import json
import sys
import urllib.request

token = sys.stdin.read().strip()
try:
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=15) as response:
        payload = json.load(response)
except Exception:
    raise SystemExit(4)

if not payload.get("ok") or not payload.get("result", {}).get("username"):
    raise SystemExit(4)
print(payload["result"]["username"])
')" || {
  printf 'Token tidak dapat diverifikasi ke Telegram. Tidak ada perubahan.\n' >&2
  exit 4
}

allowed_users="$(sed -n 's/^TELEGRAM_ALLOWED_USERS=//p' "${source_env}" | tail -n 1)"
if [[ -z "${allowed_users}" ]]; then
  printf 'Allowlist Edith tidak ditemukan. Tidak ada perubahan.\n' >&2
  exit 5
fi

temporary_file="$(mktemp "${profile_dir}/.env.XXXXXX")"
trap 'rm -f "${temporary_file}"' EXIT
awk '!/^(TELEGRAM_BOT_TOKEN|TELEGRAM_ALLOWED_USERS)=/' "${env_file}" > "${temporary_file}"
printf 'TELEGRAM_BOT_TOKEN=%s\n' "${bot_token}" >> "${temporary_file}"
printf 'TELEGRAM_ALLOWED_USERS=%s\n' "${allowed_users}" >> "${temporary_file}"
chmod 600 "${temporary_file}"
mv "${temporary_file}" "${env_file}"
trap - EXIT
unset bot_token existing_token

systemctl --user restart hermes-gateway.service
sleep 3

if systemctl --user is-active --quiet hermes-gateway.service; then
  printf 'Vission terhubung sebagai @%s. Gateway aktif.\n' "${bot_name}"
else
  printf 'Token tersimpan, tetapi gateway gagal aktif.\n' >&2
  exit 6
fi
