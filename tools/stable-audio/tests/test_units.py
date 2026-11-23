"""saudio の単体試験（torch・GPU・上流・gradio を使わない）。

実行: uvx --with pytest --with fastapi --with httpx pytest tools/stable-audio/tests/test_units.py tools/stable-audio/tests/test_rest.py -q
（saudio の import は標準ライブラリだけで通る。torch が要る saudio.engine / saudio.ui は import しない）
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

from saudio import cli, config, help_texts, params  # noqa: E402

NOW = dt.datetime(2026, 11, 23, 15, 30, 12)
SRC = params.SourceAudio(name="a.wav", data=b"RIFF....")


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def test_config_paths_are_under_tool_dir() -> None:
    for path in (config.VENDOR_DIR, config.MODELS_DIR, config.OUTPUTS_DIR, config.HF_HOME_DIR, config.HF_HUB_CACHE_DIR, config.TORCH_HOME_DIR):
        assert TOOL_DIR in path.parents


def test_port_does_not_collide_with_other_tools() -> None:
    assert config.PORT == 7864
    for other in ("audiocraft/acraft/config.py",):
        assert "PORT = 7864" not in (TOOL_DIR.parent / other).read_text(encoding="utf-8")


def test_runtime_env_overrides() -> None:
    env = config.runtime_env_overrides()
    assert env["HF_HOME"] == str(config.HF_HOME_DIR) and env["HF_HUB_CACHE"] == str(config.HF_HUB_CACHE_DIR)
    assert env["HF_HUB_OFFLINE"] == "1" and env["HF_HUB_DISABLE_TELEMETRY"] == "1" and env["GRADIO_ANALYTICS_ENABLED"] == "False"
    assert "HF_HUB_OFFLINE" not in config.runtime_env_overrides(offline=False)


def test_model_revision_is_a_full_sha() -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", config.MODEL_REVISION)
    assert re.fullmatch(r"[0-9a-f]{40}", config.UPSTREAM_COMMIT)


# --------------------------------------------------------------------------
# params
# --------------------------------------------------------------------------


def test_normalize_params_defaults() -> None:
    request = params.normalize_params("  calm \n piano ")
    assert request.prompt == "calm piano" and request.duration == params.DEFAULT_DURATION
    assert request.mode == "text" and request.source is None and request.steps == params.DEFAULT_STEPS


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"prompt": ""}, "説明文が空"),
        ({"prompt": 1}, "文字列"),
        ({"prompt": "x" * 1001}, "長すぎ"),
        ({"prompt": "x", "duration": 0.5}, "1 秒以上"),
        ({"prompt": "x", "duration": 381}, "380 秒以下"),
        ({"prompt": "x", "duration": "10"}, "数で"),
        ({"prompt": "x", "seed": -2}, "seed"),
        ({"prompt": "x", "steps": 0}, "steps"),
        ({"prompt": "x", "steps": 1.5}, "整数"),
        ({"prompt": "x", "mode": "remix"}, "モード"),
        ({"prompt": "x", "mode": "audio"}, "元の音"),
        ({"prompt": "x", "mode": "inpaint"}, "元の音"),
        ({"prompt": "x", "source": SRC}, "モードが text"),
        ({"prompt": "x", "mode": "audio", "source": SRC, "init_noise_level": 1.5}, "init_noise_level"),
        ({"prompt": "x", "mode": "inpaint", "source": SRC}, "開始"),
        ({"prompt": "x", "mode": "inpaint", "source": SRC, "inpaint_start": -1}, "0 秒以上"),
        ({"prompt": "x", "duration": 10, "mode": "inpaint", "source": SRC, "inpaint_start": 10}, "より後"),
        ({"prompt": "x", "duration": 10, "mode": "inpaint", "source": SRC, "inpaint_start": 2, "inpaint_end": 12}, "長さ（10 秒）以下"),
        ({"prompt": "x", "mode": "audio", "source": params.SourceAudio("e", b"")}, "空"),
    ],
)
def test_normalize_params_rejects_with_reason(kwargs, fragment) -> None:
    with pytest.raises(params.ParamError) as raised:
        params.normalize_params(**kwargs)
    assert fragment in str(raised.value)


def test_normalize_params_inpaint_end_defaults_to_duration_and_accepts_continuation() -> None:
    request = params.normalize_params("x", 30, mode="inpaint", source=SRC, inpaint_start=10)
    assert request.inpaint_start == 10.0 and request.inpaint_end is None
    params.check_source(request, 10.0)  # 開始 = 元の音の長さ（続き）は通る
    with pytest.raises(params.ParamError, match="超えて"):
        params.check_source(request, 8.0)


def test_check_source_limits() -> None:
    request = params.normalize_params("x", 30, mode="audio", source=SRC)
    params.check_source(request, 120.0)
    for seconds in (0.0, params.SOURCE_MAX_SECONDS + 1):
        with pytest.raises(params.ParamError):
            params.check_source(request, seconds)


def test_source_from_path_and_base64(tmp_path: Path) -> None:
    assert params.source_from_path(None) is None and params.source_from_path("") is None
    path = tmp_path / "in.wav"
    path.write_bytes(b"RIFFdata")
    assert params.source_from_path(str(path)).data == b"RIFFdata"
    with pytest.raises(params.ParamError, match="読めません"):
        params.source_from_path(str(tmp_path / "missing.wav"))
    assert params.source_from_base64(base64.b64encode(b"RIFF").decode()).data == b"RIFF"
    for bad in (None, 1, "", "!!!"):
        with pytest.raises(params.ParamError):
            params.source_from_base64(bad)
    with pytest.raises(params.ParamError, match="大きすぎ"):
        params.source_from_base64("A" * (params.SOURCE_MAX_BYTES * 2))


def test_parse_rest_body_accepts_every_documented_field() -> None:
    body = {
        "prompt": "x", "duration": 20, "seed": 3, "steps": 6, "mode": "inpaint", "audio_base64": base64.b64encode(b"RIFF").decode(),
        "init_noise_level": 0.5, "inpaint_start": 5, "inpaint_end": 15, "keep": False,
    }
    request = params.parse_rest_body(body)
    assert set(body) == set(params.REST_BODY_FIELDS)
    assert request.keep is False and request.params.mode == "inpaint" and request.params.source.data == b"RIFF"
    assert (request.params.inpaint_start, request.params.inpaint_end, request.params.steps) == (5.0, 15.0, 6)


@pytest.mark.parametrize(
    "body, fragment",
    [
        (None, "オブジェクト"),
        ({}, "prompt"),
        ({"prompt": "x", "kind": "music"}, "未知の項目があります: kind"),
        ({"prompt": "x", "keep": 1}, "keep は true か false"),
        ({"prompt": "x", "mode": 3}, "mode は文字列"),
    ],
)
def test_parse_rest_body_rejects_with_reason(body, fragment) -> None:
    with pytest.raises(params.ParamError) as raised:
        params.parse_rest_body(body)
    assert fragment in str(raised.value)


def _metadata(mode: str = "text", source_info=None) -> dict:
    source = None if mode == "text" else SRC
    extra = {"inpaint_start": 5} if mode == "inpaint" else {}
    request = params.normalize_params("calm piano", 30, 7, mode=mode, source=source, **extra)
    return params.build_metadata(
        request, resolved_seed=7, model_id=config.MODEL_REPO, sample_rate=44100, channels=2, samples=44100 * 30,
        elapsed_sec=1.234, peak_vram_mib=5000.04, device="cuda", wav_name="a.wav", now=NOW, source_info=source_info,
    )


def test_build_metadata_and_headers() -> None:
    meta = _metadata("inpaint", {"name": "x.wav", "duration_sec": 5.0, "sample_rate": 48000, "channels": 2, "wav": "a-source.wav"})
    json.dumps(meta)
    assert meta["duration_actual_sec"] == 30.0 and meta["inpaint_end_sec"] == 30.0 and meta["init_noise_level"] is None
    headers = params.build_response_headers(meta, keep=True)
    assert headers["X-Saudio-Mode"] == "inpaint" and headers["X-Saudio-Channels"] == "2" and headers["X-Saudio-File"] == "a.wav"
    assert "X-Saudio-File" not in params.build_response_headers(meta, keep=False)
    assert all(value.isascii() for value in headers.values())
    text = help_texts.format_metadata_text(meta)
    assert "部分の作り直し" in text and "5.0 〜 30.0" in text and "x.wav" in text


def test_output_stem_and_unique(tmp_path: Path) -> None:
    stem = params.build_output_stem("audio", 9, NOW)
    assert stem == "20261123-153012-audio-9"
    (tmp_path / f"{stem}.wav").write_bytes(b"")
    assert params.unique_stem(tmp_path, stem) == f"{stem}-2"


def test_list_recent_outputs(tmp_path: Path) -> None:
    meta = _metadata()
    (tmp_path / "a.wav").write_bytes(b"x")
    (tmp_path / "a.json").write_text(json.dumps(meta), encoding="utf-8")
    (tmp_path / "b.json").write_text("{broken", encoding="utf-8")
    rows = params.list_recent_outputs(tmp_path)
    assert [row["wav"] for row in rows] == ["a.wav"] and rows[0]["mode"] == "text"
    assert params.list_recent_outputs(tmp_path / "none") == []


# --------------------------------------------------------------------------
# help_texts
# --------------------------------------------------------------------------


def test_help_texts_match_params_and_api() -> None:
    assert "380" in help_texts.HEADER_MD and "Community License" in help_texts.LICENSE_MD
    for fragment in ("/api/v1/generate", "/api/v1/health", f":{config.PORT}", "application/json", "415", "503"):
        assert fragment in help_texts.API_MD, fragment
    for name in params.REST_BODY_FIELDS:
        assert f"`{name}`" in help_texts.API_MD, name
    for example in help_texts.EXAMPLES:
        params.normalize_params(example)
    assert set(help_texts.MODE_INTRO) == set(params.MODES)


# --------------------------------------------------------------------------
# cli（純粋関数）
# --------------------------------------------------------------------------


def test_build_runtime_env_and_serve_command() -> None:
    base = {"PATH": "/usr/bin"}
    env = cli.build_runtime_env(base)
    assert base == {"PATH": "/usr/bin"} and env["HF_HUB_OFFLINE"] == "1"
    assert cli.build_serve_command("/py", "0.0.0.0", 7864, None) == ["/py", "-m", "saudio.ui", "--host", "0.0.0.0", "--port", "7864"]
    assert cli.build_serve_command("/py", "h", 1, "cpu")[-2:] == ["--device", "cpu"]


def test_classify_port_and_vram() -> None:
    assert cli.classify_port_status(7864, False, False)[0]
    assert cli.classify_port_status(7864, True, True)[0]
    assert not cli.classify_port_status(7864, True, False)[0]
    assert not cli.classify_vram_status(None, 1024, False)[0]
    assert cli.classify_vram_status(20000, 1024, True)[0]
    assert cli.classify_vram_status(100, 1024, False)[0]
    assert not cli.classify_vram_status(5000, 1024, False)[0]


GOOD_PROBE = {
    "torch_version": "2.7.1+cu126", "cuda_version": "12.6", "cuda_available": True, "device_name": "NVIDIA A30",
    "flash_attn_version": "2.8.3", "soundfile_version": "0.14.0", "transformers_version": "5.24.0", "gradio_version": "5.33.0",
    "fastapi_version": "0.163.2", "stable_audio_3_ok": True,
}


def test_evaluate_probe_all_ok_and_skip_gpu() -> None:
    assert all(ok for _, ok, _ in cli.evaluate_probe(GOOD_PROBE, skip_gpu=False))
    probe = {key: value for key, value in GOOD_PROBE.items() if key not in ("cuda_available", "device_name")}
    assert all(ok for _, ok, _ in cli.evaluate_probe(probe, skip_gpu=True))


@pytest.mark.parametrize(
    "changes, label_fragment",
    [
        ({"torch_version": "2.9.1+cu126"}, "torch が 2.7.1"),
        ({"cuda_version": "13.2"}, "12.6"),
        ({"flash_attn_version": None, "flash_attn_error": "ModuleNotFoundError"}, "flash_attn"),
        ({"stable_audio_3_ok": None, "stable_audio_3_error": "ImportError"}, "stable_audio_3"),
        ({"device_name": "RTX"}, "A30"),
    ],
)
def test_evaluate_probe_flags_problems(changes, label_fragment) -> None:
    probe = {**GOOD_PROBE, **changes}
    bad = [label for label, ok, _ in cli.evaluate_probe(probe, skip_gpu=False) if not ok]
    assert any(label_fragment in label for label in bad), bad


def test_env_probe_formats_to_valid_python() -> None:
    for skip in (True, False):
        compile(cli._ENV_PROBE.format(skip_gpu=skip), "<probe>", "exec")


def _make_snapshot(cache: Path, revision: str, files=None) -> Path:
    snapshot = cli.hub_repo_dir(cache, config.MODEL_REPO) / "snapshots" / revision
    for name in files if files is not None else cli.required_model_files():
        (snapshot / name).parent.mkdir(parents=True, exist_ok=True)
        (snapshot / name).write_bytes(b"x")
    return snapshot


def test_check_model_cache(tmp_path: Path) -> None:
    revision = config.MODEL_REVISION
    assert not cli.check_model_cache(tmp_path, config.MODEL_REPO, revision)[0]
    _make_snapshot(tmp_path, revision, files=cli.required_model_files()[:-1])
    ok, detail = cli.check_model_cache(tmp_path, config.MODEL_REPO, revision)
    assert not ok and "足りない" in detail
    _make_snapshot(tmp_path, revision)
    ok, detail = cli.check_model_cache(tmp_path, config.MODEL_REPO, revision)
    assert not ok and "refs/main" in detail
    ref = cli.hub_repo_dir(tmp_path, config.MODEL_REPO) / "refs" / "main"
    ref.parent.mkdir(parents=True)
    ref.write_text(revision)
    assert cli.check_model_cache(tmp_path, config.MODEL_REPO, revision)[0]


def test_required_files_include_the_text_encoder() -> None:
    files = cli.required_model_files()
    assert "model.safetensors" in files and "model_config.json" in files
    assert f"{config.TEXT_ENCODER_SUBFOLDER}/model.safetensors" in files


def test_read_token_prefers_env_and_ignores_blank(tmp_path: Path) -> None:
    token_file = tmp_path / "hf-token"
    assert cli.read_token({}, token_file) is None
    token_file.write_text("  \n")
    assert cli.read_token({}, token_file) is None
    token_file.write_text("hf_file\n")
    assert cli.read_token({}, token_file) == "hf_file"
    assert cli.read_token({"HF_TOKEN": " hf_env "}, token_file) == "hf_env"


def test_fetch_models_without_token_explains_and_returns_3(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(config, "HF_HUB_CACHE_DIR", tmp_path / "hub")
    monkeypatch.setattr(config, "HF_TOKEN_FILE", tmp_path / "no-token")
    monkeypatch.setattr(config, "VENV_PYTHON", Path(sys.executable))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    assert cli.main(["fetch-models"]) == 3
    err = capsys.readouterr().err
    assert "規約に同意" in err and config.MODEL_REPO in err


def test_generate_rejects_bad_params_before_loading_anything(capsys) -> None:
    assert cli.main(["generate", "--prompt", "", "--duration", "5"]) == 2
    assert "説明文が空" in capsys.readouterr().err
    assert "torch" not in sys.modules


# --------------------------------------------------------------------------
# systemd の雛形・導入スクリプト・ファイル構成
# --------------------------------------------------------------------------

UNIT_TEXT = (TOOL_DIR / "systemd" / "stable-audio.service.example").read_text(encoding="utf-8")


def _unit_line(prefix: str) -> str:
    lines = [line for line in UNIT_TEXT.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1, (prefix, lines)
    return lines[0]


def _units(line: str) -> list[str]:
    return [token.removesuffix(".service") for token in line.split("=", 1)[1].split() if token.endswith(".service")]


def test_unit_conflicts_with_every_gpu_service_and_orders_after_them() -> None:
    assert _units(_unit_line("Conflicts=")) == list(cli.GPU_CONFLICT_UNITS)
    assert _units(_unit_line("After=")) == list(cli.GPU_CONFLICT_UNITS)


def test_gpu_conflict_units_match_portal_exclusive_keys() -> None:
    portal_text = (TOOL_DIR.parent / "portal" / "portal.py").read_text(encoding="utf-8")
    match = re.search(r"GPU_EXCLUSIVE_KEYS = \(([^)]*)\)", portal_text)
    assert match is not None
    assert set(re.findall(r'"([^"]+)"', match.group(1))) == set(cli.GPU_CONFLICT_UNITS) | {"stable-audio"}


def test_no_other_unit_template_orders_after_stable_audio() -> None:
    for template in TOOL_DIR.parent.glob("*/systemd/*.service.example"):
        if template.parent.parent == TOOL_DIR:
            continue
        assert "After=stable-audio" not in template.read_text(encoding="utf-8")
        for line in template.read_text(encoding="utf-8").splitlines():
            if line.startswith("After="):
                assert "stable-audio.service" not in line, template


def test_unit_does_not_restart_when_setup_is_incomplete(tmp_path: Path, monkeypatch) -> None:
    assert _unit_line("RestartPreventExitStatus=") == f"RestartPreventExitStatus={cli.SETUP_INCOMPLETE_EXIT}"
    # 重みが無いと serve はその終了コードで止まる（VRAM の確認は省き、ほかは揃っている状態にする）
    monkeypatch.setattr(cli.ollama, "loaded_models", lambda: [])
    monkeypatch.setattr(config, "VENDOR_DIR", tmp_path)
    monkeypatch.setattr(config, "VENV_PYTHON", Path(sys.executable))
    monkeypatch.setattr(config, "HF_HUB_CACHE_DIR", tmp_path / "hub")
    assert cli.main(["serve", "--skip-vram-check"]) == cli.SETUP_INCOMPLETE_EXIT


def test_unit_template_uses_placeholder_and_standard_layout() -> None:
    assert "@REPO_ROOT@/tools/stable-audio/run-stable-audio serve" in _unit_line("ExecStart=")
    assert _unit_line("WorkingDirectory=") == "WorkingDirectory=@REPO_ROOT@/tools/stable-audio"
    assert _unit_line("EnvironmentFile=") == "EnvironmentFile=-%h/.config/stable-audio/stable-audio.env"
    assert "7864" in UNIT_TEXT.splitlines()[1]
    assert not re.search(r"/home/|192\.168\.", UNIT_TEXT)


def test_setup_script_pins_match_config() -> None:
    setup_text = (TOOL_DIR / "setup-server.sh").read_text(encoding="utf-8")
    assert f'UPSTREAM_URL="{config.UPSTREAM_URL}"' in setup_text
    assert f'UPSTREAM_COMMIT="{config.UPSTREAM_COMMIT}"' in setup_text
    assert config.UPSTREAM_COMMIT in (TOOL_DIR / "requirements.lock.txt").read_text(encoding="utf-8")
    assert f'TORCH_VERSION="{cli.TORCH_EXPECTED_VERSION_PREFIX}+cu126"' in setup_text
    assert f'FLASH_ATTN_VERSION="{cli.FLASH_ATTN_EXPECTED_VERSION_PREFIX}"' in setup_text
    assert "cp312" in setup_text and 'PYTHON_VERSION="3.12"' in setup_text
    assert subprocess.run(["bash", "-n", str(TOOL_DIR / "setup-server.sh")], capture_output=True).returncode == 0


def test_lock_file_has_no_torch_and_pins_everything() -> None:
    lock = (TOOL_DIR / "requirements.lock.txt").read_text(encoding="utf-8")
    lines = [line for line in lock.splitlines() if line and not line.startswith("#")]
    pins = dict(line.split("==", 1) for line in lines)
    assert len(pins) == len(lines)  # すべて == で固定
    assert "torch" not in pins and "torchaudio" not in pins and "flash-attn" not in pins
    assert pins["gradio"] == "5.33.0" and pins["numpy"].startswith("2.") and pins["transformers"].startswith("5.")


def test_run_script_is_executable_bash() -> None:
    script = TOOL_DIR / "run-stable-audio"
    assert script.stat().st_mode & 0o111
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0


def test_python_sources_compile() -> None:
    for path in (TOOL_DIR / "saudio").glob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
