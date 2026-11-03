#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../../.."
log_dir=$(mktemp -d /tmp/aie01-benchmark.XXXXXX)
echo "ログ保存先: $log_dir"

models=(gemini-3.5-flash-lite gemini-3.8-flash)
questions=(
  'LoRAとは何ですか？100文字程度で説明してください。'
  'AIE2026の講座の宿題にある「3レベル」とは何ですか？100文字程度で説明してください。'
)

count=0
for model in "${models[@]}"; do
  for question_index in "${!questions[@]}"; do
    for run in 1 2 3; do
      count=$((count + 1))
      log_file="$log_dir/${model}-q$((question_index + 1))-${run}.log"
      echo "=== $count/12: $model / 質問$((question_index + 1)) / ${run}回目 ==="
      if ! AIE_MODEL="$model" uv run python -m app.ask "${questions[$question_index]}" 2>&1 | tee "$log_file"; then
        echo "エラーのため停止しました。ログ: $log_file" >&2
        exit 1
      fi
      if (( count < 12 )); then
        sleep 20
      fi
    done
  done
done

echo "全12回の実行が完了しました。ログ保存先: $log_dir"