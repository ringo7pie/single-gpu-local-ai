"""qtts の単体試験。GPU・torch・qwen-tts・ネットワークを使わない。

実行: uvx --with pytest pytest tools/qwen3-tts/tests/test_units.py -q
（qtts の import は標準ライブラリだけで通る）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_DIR.parents[1]
sys.path.insert(0, str(TOOL_DIR))

from qtts import cli, config  # noqa: E402

SETUP_SCRIPT = TOOL_DIR / "setup-server.sh"
UNIT_TEMPLATE = TOOL_DIR / "systemd" / "qwen3-tts.service.example"
LOCK_FILE = TOOL_DIR / "requirements.lock.txt"


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def test_config_paths_are_under_tool_dir() -> None:
    for path in (config.MODELS_DIR, config.OUTPUTS_DIR, config.VENV_PYTHON, config.DEMO_BIN):
        assert TOOL_DIR in path.parents
    for path in (config.HF_HOME_DIR, config.MODEL_DIR):
        assert config.MODELS_DIR in path.parents


def test_config_port_does_not_collide_with_other_services() -> None:
    # tools/portal/portal.py の SERVICES と同じポート配置と重ならない
    taken = {8080, 8081, 8082, 8083, 8084, 8085, 8086, 7860, 7861, 7862, 8188, 11434}
    assert config.PORT == 7863 and config.PORT not in taken


def test_runtime_env_overrides_point_inside_models_dir_and_disable_telemetry() -> None:
    overrides = config.runtime_env_overrides()
    assert Path(overrides["HF_HOME"]).is_relative_to(config.MODELS_DIR)
    assert overrides["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert overrides["GRADIO_ANALYTICS_ENABLED"] == "False"


def test_model_revision_is_a_full_commit_sha() -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", config.MODEL_REVISION)
    assert config.MODEL_REPO_ID.endswith("VoiceDesign")


@pytest.mark.parametrize(
    "environ, expected",
    [
        ({}, ("bfloat16", False)),
        ({config.DTYPE_ENV_VAR: "  "}, ("bfloat16", False)),
        ({config.DTYPE_ENV_VAR: "float16"}, ("float16", False)),
        ({config.DTYPE_ENV_VAR: "int4"}, ("bfloat16", False)),
        ({config.FLASH_ATTN_ENV_VAR: "1"}, ("bfloat16", True)),
        ({config.FLASH_ATTN_ENV_VAR: " true "}, ("bfloat16", True)),
        ({config.FLASH_ATTN_ENV_VAR: "0"}, ("bfloat16", False)),
        ({config.FLASH_ATTN_ENV_VAR: "no"}, ("bfloat16", False)),
    ],
)
def test_serve_options_from_env(environ, expected) -> None:
    assert config.serve_options_from_env(environ) == expected


# --------------------------------------------------------------------------
# cli の純粋関数
# --------------------------------------------------------------------------


def test_build_runtime_env_does_not_mutate_base() -> None:
    base = {"PATH": "/usr/bin", "HF_HOME": "/elsewhere"}
    env = cli.build_runtime_env(base)
    assert base == {"PATH": "/usr/bin", "HF_HOME": "/elsewhere"}
    assert env["PATH"] == "/usr/bin" and env["HF_HOME"] == str(config.HF_HOME_DIR)


def test_build_serve_command_always_states_flash_attn_and_limits_concurrency() -> None:
    command = cli.build_serve_command("/v/qwen-tts-demo", "/m", "0.0.0.0", 7863, "bfloat16", False, None)
    assert command == [
        "/v/qwen-tts-demo", "/m", "--ip", "0.0.0.0", "--port", "7863", "--dtype", "bfloat16",
        "--concurrency", "1", "--no-flash-attn",
    ]
    command = cli.build_serve_command("/v/qwen-tts-demo", "/m", "127.0.0.1", 9000, "float16", True, "cuda:0")
    assert command[-3:] == ["--flash-attn", "--device", "cuda:0"]
    assert "--no-flash-attn" not in command


def test_classify_port_status() -> None:
    assert cli.classify_port_status(7863, False, False)[0] is True
    assert cli.classify_port_status(7863, True, True)[0] is True
    assert cli.classify_port_status(7863, True, False)[0] is False


def test_classify_vram_status() -> None:
    assert cli.classify_vram_status(None, 1024, False)[0] is False
    assert cli.classify_vram_status(20000, 1024, True)[0] is True
    assert cli.classify_vram_status(500, 1024, False)[0] is True
    assert cli.classify_vram_status(2000, 1024, False)[0] is False


def _probe_ok(**overrides):
    probe = {
        "torch_version": "2.9.1+cu126",
        "cuda_version": "12.6",
        "cuda_available": True,
        "device_name": "NVIDIA A30",
        "transformers_version": "4.57.3",
        "gradio_version": "6.17.3",
        "soundfile_version": "0.14.0",
        "qwen_tts_version": config.QWEN_TTS_VERSION,
    }
    probe.update(overrides)
    return probe


def test_evaluate_probe_all_ok() -> None:
    rows = cli.evaluate_probe(_probe_ok(), skip_gpu=False)
    assert all(ok for _, ok, _ in rows)
    assert any("A30" in detail for _, _, detail in rows)


def test_evaluate_probe_reports_errors() -> None:
    rows = dict((label, ok) for label, ok, _ in cli.evaluate_probe({"torch_error": "boom"}, skip_gpu=True))
    assert rows["torch の import"] is False
    rows = dict((label, ok) for label, ok, _ in cli.evaluate_probe(_probe_ok(cuda_available=False), skip_gpu=False))
    assert rows["CUDA"] is False
    rows = dict((label, ok) for label, ok, _ in cli.evaluate_probe(_probe_ok(qwen_tts_version="0.0.5"), skip_gpu=False))
    assert rows["qwen-tts の import"] is False
    rows = dict((label, ok) for label, ok, _ in cli.evaluate_probe(_probe_ok(gradio_error="x"), skip_gpu=False))
    assert rows["gradio の import"] is False


def test_evaluate_probe_skip_gpu_does_not_require_cuda() -> None:
    probe = _probe_ok()
    del probe["cuda_available"]
    rows = dict((label, ok) for label, ok, _ in cli.evaluate_probe(probe, skip_gpu=True))
    assert rows["CUDA"] is True


def test_missing_model_files(tmp_path: Path) -> None:
    assert cli.missing_model_files(tmp_path) == list(config.REQUIRED_MODEL_FILES)
    for name in config.REQUIRED_MODEL_FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x")
    assert cli.missing_model_files(tmp_path) == []


def test_parser_subcommands() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["serve"])
    assert args.host == config.HOST and args.port == config.PORT and args.device is None
    assert parser.parse_args(["doctor", "--skip-gpu"]).skip_gpu is True
    assert parser.parse_args(["fetch-models"]).func is cli.cmd_fetch_models


# --------------------------------------------------------------------------
# 雛形・導入スクリプト・固定版の突き合わせ
# --------------------------------------------------------------------------


def _unit_values(key: str) -> list[str]:
    for line in UNIT_TEMPLATE.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].split()
    raise AssertionError(f"{key}= が雛形に無い")


def test_unit_conflicts_and_after_match_gpu_conflict_units() -> None:
    expected = [f"{unit}.service" for unit in cli.GPU_CONFLICT_UNITS]
    assert _unit_values("Conflicts") == expected
    assert _unit_values("After") == ["network.target", *expected]


def test_other_gpu_units_do_not_order_after_qwen3_tts() -> None:
    # After= は qwen3-tts 側だけに書く（両側に書くと順序が輪になる）。輪になりうるのは、qwen3-tts が After= に挙げた
    # ユニットだけ。qwen3-tts より後に足したユニット（2026-11-23 の stable-audio など）は、同じ型で qwen3-tts の後ろに並ぶ
    # （After=qwen3-tts.service を持つ）が、qwen3-tts の側はそれを After= に挙げないので輪にならない。
    for template in REPO_ROOT.glob("tools/*/systemd/*.service.example"):
        if template == UNIT_TEMPLATE or template.name.removesuffix(".service.example") not in cli.GPU_CONFLICT_UNITS:
            continue
        assert "qwen3-tts.service" not in "".join(
            line for line in template.read_text(encoding="utf-8").splitlines(keepends=True) if line.startswith("After=")
        ), template


def test_unit_template_uses_repo_root_placeholder_and_runner() -> None:
    text = UNIT_TEMPLATE.read_text(encoding="utf-8")
    assert "ExecStart=@REPO_ROOT@/tools/qwen3-tts/run-qwen3-tts serve" in text
    assert "EnvironmentFile=-%h/.config/qwen3-tts/qwen3-tts.env" in text
    assert "MemoryMax=" in text and "MemoryHigh=" in text


def test_lock_pins_qwen_tts_version_and_excludes_torch() -> None:
    lines = [line.strip() for line in LOCK_FILE.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    assert f"qwen-tts=={config.QWEN_TTS_VERSION}" in lines
    assert not [line for line in lines if re.match(r"(torch|torchaudio|triton|nvidia-)", line)]
    assert all("==" in line for line in lines)


def test_setup_script_is_strict_and_does_not_enable_or_sudo() -> None:
    text = SETUP_SCRIPT.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert "systemctl --user enable" not in text
    assert not re.search(r"^\s*sudo\b", text, re.MULTILINE)
    assert "render_unit_file" in text and "qwen3-tts.service.example" in text
