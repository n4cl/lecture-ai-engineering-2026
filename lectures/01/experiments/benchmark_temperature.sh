#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../.."
log_dir=$(mktemp -d lectures/01/logs/temperature0.XXXXXX)
echo "ログ保存先: $log_dir"

questions=(
  'LoRAとは何ですか？100文字程度で説明してください。'
  'AIE2026の講座の宿題にある「3レベル」とは何ですか？100文字程度で説明してください。'
)

for i in "${!questions[@]}"; do
  for run in 1 2 3; do
    log_file="$log_dir/q$((i + 1))-${run}.log"
    if ! {
      echo "=== 質問$((i + 1)) / ${run}回目 / temperature=0 ==="
      AIE_MODEL=gemini-3.5-flash-lite uv run python -m app.ask \
        "${questions[$i]}" --temperature 0
    } 2>&1 | tee "$log_file"; then
      echo "エラーのため停止しました。ログ: $log_file"
      exit 1
    fi
  done
done

echo "全6回の実行が完了しました。ログ保存先: $log_dir"
