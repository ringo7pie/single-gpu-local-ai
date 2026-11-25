#!/usr/bin/env bash
# Wan-Alpha v2.0 を INT8 で 1 本作り、秒数・VRAM の最大値（nvidia-smi の機体全体、1 秒ごと）を output/<タグ>/trial.json に書く。
#   run-trial.sh [タグ] [幅*高さ] [コマ数]
# GPU を使うほかのサービス（ComfyUI など）は先に止めておくこと。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAG="${1:-v2_832x480_f81}"
SIZE="${2:-832*480}"
FRAMES="${3:-81}"
OUT="$HERE/output/$TAG"
PROMPT="${WAN_ALPHA_PROMPT:-a swirling cloud of glowing violet magic mist, soft wisps, centered, no background}"
LIGHTX2V="${LIGHTX2V_PATH:-$HERE/../comfyui/vendor/ComfyUI/models/loras/lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank64_bf16.safetensors}"
mkdir -p "$OUT"
printf '%s\n' "$PROMPT" > "$OUT/prompt.txt"
nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -l 1 > "$OUT/vram.csv" &
SMI_PID=$!
trap 'kill "$SMI_PID" 2>/dev/null || true' EXIT
START=$(date +%s.%N)
set +e
"$HERE/.venv/bin/python" "$HERE/run_int8.py" \
  --size "$SIZE" --frame_num "$FRAMES" \
  --ckpt_dir "$HERE/models/Wan2.1-T2V-14B" \
  --vae_lora_checkpoint "$HERE/models/Wan-Alpha-v2.0/decoder.bin" \
  --lora_path "$HERE/models/Wan-Alpha-v2.0/t2v.safetensors" \
  --lightx2v_path "$LIGHTX2V" \
  --cache_path_mask "$HERE/models/Wan-Alpha-v2.0/gauss_mask" \
  --sample_guide_scale 1.0 --sample_steps 4 --lora_ratio 1.0 --lora_prefix "" --alpha_shift_mean 0.05 \
  --t5_cpu --offload_model True --base_seed 42 \
  --prompt_file "$OUT/prompt.txt" --output_dir "$OUT" 2>&1 | tee "$OUT/run.log"
STATUS=${PIPESTATUS[0]}
set -e
END=$(date +%s.%N)
kill "$SMI_PID" 2>/dev/null || true
PEAK=$(sort -n "$OUT/vram.csv" | tail -1)
python3 -c "import json,sys; print(json.dumps({'tag': sys.argv[1], 'exit': int(sys.argv[2]), 'wall_seconds': round(float(sys.argv[4]) - float(sys.argv[3]), 1), 'vram_peak_mib': int(sys.argv[5] or 0)}))" \
  "$TAG" "$STATUS" "$START" "$END" "$PEAK" | tee "$OUT/trial.json"
exit "$STATUS"
