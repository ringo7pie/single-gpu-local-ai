#!/usr/bin/env bash
# Wan-Alpha v2.0 の推論に要る重みを models/ に取り、sha256 を照合する（合わなければ消して失敗にする）。
#   models/Wan2.1-T2V-14B/  土台（Wan-AI/Wan2.1-T2V-14B。説明用の画像 assets/・examples/ は取らない）
#   models/Wan-Alpha-v2.0/  t2v.safetensors（DoRA）・decoder.bin（VAE）・gauss_mask
# LightX2V の LoRA は ComfyUI に入れたもの（tools/comfyui/vendor/ComfyUI/models/loras/）を読むだけにする。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS="$HERE/models"
HF="https://huggingface.co"
# repo  ファイル  サイズ  sha256（空は小さい非 LFS のファイルで、サイズだけ照合する）
LIST="
Wan-AI/Wan2.1-T2V-14B config.json 250 -
Wan-AI/Wan2.1-T2V-14B LICENSE.txt 11357 -
Wan-AI/Wan2.1-T2V-14B diffusion_pytorch_model.safetensors.index.json 96805 -
Wan-AI/Wan2.1-T2V-14B diffusion_pytorch_model-00001-of-00006.safetensors 9887603256 569d54a07279b89f8281421fccf27ee2459ea853ce6845d3536b8664b0070078
Wan-AI/Wan2.1-T2V-14B diffusion_pytorch_model-00002-of-00006.safetensors 9839059648 b17ff172f262b4da91c31e8c46d3b7707f62cecfff6e18dd0072ab29eb350e46
Wan-AI/Wan2.1-T2V-14B diffusion_pytorch_model-00003-of-00006.safetensors 9839059744 741fd1508cd0288f0bb0f4fb3df6734da521d5ff23746170c848564a367e2cea
Wan-AI/Wan2.1-T2V-14B diffusion_pytorch_model-00004-of-00006.safetensors 9839059744 47b08ba289b127fc64979bc877ee07e1c87aeb8eb8cd47bcfd112682847981ba
Wan-AI/Wan2.1-T2V-14B diffusion_pytorch_model-00005-of-00006.safetensors 9839059744 29e35f4cf0e3a61f4726c960f1babf71d65677a8c99fe704257d665c9498ee88
Wan-AI/Wan2.1-T2V-14B diffusion_pytorch_model-00006-of-00006.safetensors 7910235256 91ac087a40b814331e87f5f015af11ba999275d48d2af49591b6bf7727d3c146
Wan-AI/Wan2.1-T2V-14B Wan2.1_VAE.pth 507609880 38071ab59bd94681c686fa51d75a1968f64e470262043be31f7a094e442fd981
Wan-AI/Wan2.1-T2V-14B models_t5_umt5-xxl-enc-bf16.pth 11361920418 7cace0da2b446bbbbc57d031ab6cf163a3d59b366da94e5afe36745b746fd81d
Wan-AI/Wan2.1-T2V-14B google/umt5-xxl/special_tokens_map.json 6623 -
Wan-AI/Wan2.1-T2V-14B google/umt5-xxl/spiece.model 4548313 e3909a67b780650b35cf529ac782ad2b6b26e6d1f849d3fbb6a872905f452458
Wan-AI/Wan2.1-T2V-14B google/umt5-xxl/tokenizer.json 16837417 6e197b4d3dbd71da14b4eb255f4fa91c9c1f2068b20a2de2472967ca3d22602b
Wan-AI/Wan2.1-T2V-14B google/umt5-xxl/tokenizer_config.json 61728 -
htdong/Wan-Alpha-v2.0 t2v.safetensors 311645240 514ecad893df2153ff92dac4a655331d4f6b4a483b69c85c7fbc3c3e6f2ab73f
htdong/Wan-Alpha-v2.0 decoder.bin 635538952 831fcf189cd1c3a369bd1256eaa3518ad3c2ea3505780df7fa8d0eda343620be
htdong/Wan-Alpha-v2.0 gauss_mask 1372110 61ad52d8860c013d84771adb22648771dc1f9c14a11604a4da772d4a54bbcff0
"
failures=0
while read -r repo name size sha
do
  [ -n "$repo" ] || continue
  target="$MODELS/${repo#*/}/$name"
  mkdir -p "$(dirname "$target")"
  if [ ! -f "$target" ] || [ "$(stat -c %s "$target")" != "$size" ]; then
    echo "取得: $repo/$name（$size バイト）"
    curl -L --fail --retry 3 -sS -C - -o "$target.part" "$HF/$repo/resolve/main/$name"
    mv "$target.part" "$target"
  fi
  actual_size="$(stat -c %s "$target")"
  if [ "$actual_size" != "$size" ]; then
    echo "NG サイズ: $name（$actual_size、期待値 $size）。消します" >&2; rm -f "$target"; failures=$((failures + 1)); continue
  fi
  if [ "$sha" != "-" ]; then
    actual_sha="$(sha256sum "$target" | cut -d' ' -f1)"
    if [ "$actual_sha" != "$sha" ]; then
      echo "NG sha256: $name（$actual_sha、期待値 $sha）。消します" >&2; rm -f "$target"; failures=$((failures + 1)); continue
    fi
    echo "OK sha256: $repo/$name"
  else
    echo "OK サイズ: $repo/$name"
  fi
done <<< "$LIST"
echo "失敗 $failures 件"
[ "$failures" -eq 0 ]
