#!/bin/sh
# Hermes 패치 관리: ./patches/apply.sh [status|apply|revert]  (기본: apply)
# HERMES_DIR로 Hermes 위치를 바꿀 수 있습니다(기본 ~/.hermes/hermes-agent).
set -eu

HERMES_DIR="${HERMES_DIR:-$HOME/.hermes/hermes-agent}"
PATCH="$(cd "$(dirname "$0")" && pwd)/hermes-keep-recovered-reply.patch"

[ -f "$PATCH" ] || { echo "패치 파일이 없습니다: $PATCH" >&2; exit 1; }
[ -d "$HERMES_DIR" ] || { echo "Hermes 폴더가 없습니다: $HERMES_DIR" >&2; exit 1; }

g() { git -C "$HERMES_DIR" "$@"; }

state() {
  if g apply -R --check "$PATCH" >/dev/null 2>&1; then echo applied
  elif g apply --check "$PATCH" >/dev/null 2>&1; then echo clean
  else echo conflict; fi
}

restart_note() { echo "Hermes Desktop을 완전히 종료했다가 다시 켜야 반영됩니다."; }

case "${1:-apply}" in
  status)
    case "$(state)" in
      applied)  echo "적용됨" ;;
      clean)    echo "적용 안 됨 (./patches/apply.sh 로 적용)" ;;
      conflict) echo "충돌: Hermes 코드가 바뀌어 패치가 맞지 않습니다. 패치를 다시 만들어야 합니다." ;;
    esac ;;
  apply)
    case "$(state)" in
      applied)  echo "이미 적용되어 있습니다." ;;
      clean)    g apply "$PATCH" && echo "적용했습니다." && restart_note ;;
      conflict) echo "적용 실패: Hermes 코드가 바뀌어 패치가 맞지 않습니다. 패치를 다시 만들어야 합니다." >&2; exit 1 ;;
    esac ;;
  revert)
    case "$(state)" in
      applied)  g apply -R "$PATCH" && echo "되돌렸습니다." && restart_note ;;
      clean)    echo "적용되어 있지 않습니다." ;;
      conflict) echo "되돌리기 실패: 현재 코드가 패치와 맞지 않습니다." >&2; exit 1 ;;
    esac ;;
  *)
    echo "사용법: $0 [status|apply|revert]" >&2; exit 2 ;;
esac
