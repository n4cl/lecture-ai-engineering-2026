cd "$(dirname "$0")/../../.."
set -euo pipefail
log_dir=$(mktemp -d /tmp/aie01-flash.XXXXXX)
echo "ログ保存先: $log_dir"

questions=(
  'LoRAとは何ですか？100文字程度で説明してください。'
  'AIE2026の講座の宿題にある「3レベル」とは何ですか？100文字程度で説明してください。'
)

for i in "${!questions[@]}"; do
  for run in 1 2 3; do
    echo "=== 質問$((i + 1)) / ${run}回目 ==="
    if ! AIE_MODEL=gemini-3.1-flash-lite uv run python -m app.ask \
      "${questions[$i]}" 2>&1 | tee "$log_dir/q$((i + 1))-${run}.log"; then
      echo "エラーのため停止しました。ログ保存先: $log_dir"
      exit 1
    fi
  done
done