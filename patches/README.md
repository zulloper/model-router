# Hermes 패치

## hermes-keep-recovered-reply.patch (B안)

프로바이더가 `content_policy`로 하드 거부한 턴을 Model Router가 복구하면, 원래 Hermes는 그 턴을 저장본에서
지웁니다(Desktop에 "refused by the safety filter"만 남음). 이 패치는 `transform_llm_output`이 답을 바꾼
경우에만 그 턴을 정상 완료 턴(사용자 행 + 도구 라운드 + 복구된 답)으로 저장합니다. 그 외의 거부는 기존대로 지웁니다.

주의: 거부된 요청이 모델 문맥에 남으므로 다음 턴에서 필터가 다시 걸릴 수 있습니다(그때마다 사다리가 다시 돕니다).
문제가 되면 A안(화면 전용 행)으로 바꿉니다.

사용법 (저장소 폴더에서, 적용·되돌린 뒤에는 Hermes Desktop 재시작):

```bash
./patches/apply.sh status   # 적용됨 / 적용 안 됨 / 충돌
./patches/apply.sh          # 적용 (Hermes 업데이트 후마다), 이미 적용돼 있으면 아무것도 안 함
./patches/apply.sh revert   # 되돌리기
```

Hermes 위치가 다르면 `HERMES_DIR=/path/to/hermes-agent ./patches/apply.sh`.
"충돌"이 나오면 Hermes 코드가 바뀐 것이니 패치를 새 코드에 맞게 다시 만들어야 합니다.
