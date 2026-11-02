"""acraft の単体試験。GPU・torch・audiocraft・ネットワークを使わない。

実行: uvx --with pytest pytest tools/audiocraft/tests/test_units.py -q
（acraft の import は標準ライブラリだけで通る。torch が要る acraft.engine / acraft.ui は import しない）
"""

from __future__ import annotations

import datetime as dt
import json
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

from acraft import cli, config, help_texts, params  # noqa: E402

NOW = dt.datetime(2026, 10, 27, 15, 30, 12)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def test_config_paths_are_under_tool_dir() -> None:
    for path in (config.VENDOR_DIR, config.MODELS_DIR, config.OUTPUTS_DIR, config.VENV_PYTHON):
        assert TOOL_DIR in path.parents
    for path in (config.AUDIOCRAFT_CACHE_DIR, config.HF_HOME_DIR, config.TORCH_HOME_DIR):
        assert config.MODELS_DIR in path.parents


def test_config_port_does_not_collide_with_other_services() -> None:
    # tools/portal/portal.py の SERVICES と同じポート配置（8080〜8086・7860・7861・8188・11434）と重ならない
    taken = {8080, 8081, 8082, 8083, 8084, 8085, 8086, 7860, 7861, 8188, 11434}
    assert config.PORT == 7862 and config.PORT not in taken


def test_runtime_env_overrides_point_inside_models_dir_and_disable_telemetry() -> None:
    overrides = config.runtime_env_overrides()
    for name in ("AUDIOCRAFT_CACHE_DIR", "HF_HOME", "TORCH_HOME"):
        assert Path(overrides[name]).is_relative_to(config.MODELS_DIR)
    assert overrides["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert overrides["GRADIO_ANALYTICS_ENABLED"] == "False"


@pytest.mark.parametrize(
    "environ, expected",
    [
        ({}, (config.DEFAULT_MUSIC_MODEL, config.DEFAULT_SOUND_MODEL)),
        ({config.MUSIC_MODEL_ENV_VAR: "  "}, (config.DEFAULT_MUSIC_MODEL, config.DEFAULT_SOUND_MODEL)),
        (
            {config.MUSIC_MODEL_ENV_VAR: "facebook/musicgen-stereo-medium", config.SOUND_MODEL_ENV_VAR: " x/y "},
            ("facebook/musicgen-stereo-medium", "x/y"),
        ),
    ],
)
def test_model_ids_from_env(environ, expected) -> None:
    assert config.model_ids_from_env(environ) == expected


def test_default_models_are_the_medium_ones() -> None:
    assert config.DEFAULT_MUSIC_MODEL == "facebook/musicgen-medium"
    assert config.DEFAULT_SOUND_MODEL == "facebook/audiogen-medium"


# --------------------------------------------------------------------------
# params
# --------------------------------------------------------------------------


def test_kind_specs_cover_all_kinds_and_defaults_are_inside_range() -> None:
    assert set(params.KIND_SPECS) == set(params.KINDS)
    for spec in params.KIND_SPECS.values():
        assert spec.min_duration <= spec.default_duration <= spec.max_duration
    # 上流のモデルカードの値
    assert params.KIND_SPECS[params.KIND_MUSIC].sample_rate == 32000
    assert params.KIND_SPECS[params.KIND_SOUND].sample_rate == 16000


def test_normalize_params_accepts_defaults_and_cleans_prompt() -> None:
    request = params.normalize_params("music", "  calm   piano\nmelody ", 10)
    assert request.prompt == "calm piano melody"
    assert request.duration == 10.0 and request.seed == params.SEED_RANDOM
    assert (request.top_k, request.top_p, request.temperature, request.cfg_coef) == (250, 0.0, 1.0, 3.0)


def test_normalize_params_accepts_gradio_style_floats_for_integers() -> None:
    request = params.normalize_params("sound", "dog barking", 5.0, 42.0, 100.0)
    assert request.seed == 42 and request.top_k == 100 and isinstance(request.seed, int)


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"kind": "video"}, "種類"),
        ({"prompt": ""}, "説明文が空"),
        ({"prompt": "   \n "}, "説明文が空"),
        ({"prompt": 123}, "説明文は文字列"),
        ({"prompt": "a" * (params.PROMPT_MAX_CHARS + 1)}, "長すぎ"),
        ({"duration": 0}, "長さ"),
        ({"duration": 31}, "30 秒以下"),
        ({"duration": "10"}, "長さ は数"),
        ({"duration": float("nan")}, "有限"),
        ({"seed": -2}, "seed"),
        ({"seed": params.SEED_MAX + 1}, "seed"),
        ({"seed": 1.5}, "整数"),
        ({"top_k": -1}, "top_k"),
        ({"top_k": 0, "top_p": 0.0}, "両方 0"),
        ({"top_p": 1.5}, "top_p"),
        ({"temperature": 0}, "temperature"),
        ({"cfg_coef": 11}, "cfg_coef"),
        ({"seed": True}, "seed は数"),
    ],
)
def test_normalize_params_rejects_bad_values_with_japanese_reason(kwargs, fragment) -> None:
    arguments = {"kind": "music", "prompt": "calm piano", "duration": 10}
    arguments.update(kwargs)
    with pytest.raises(params.ParamError) as raised:
        params.normalize_params(**arguments)
    assert fragment in str(raised.value)


def test_sound_duration_limit_is_lower_than_music() -> None:
    params.normalize_params("music", "x", 30)
    params.normalize_params("sound", "x", 20)
    with pytest.raises(params.ParamError, match="20 秒以下"):
        params.normalize_params("sound", "x", 21)


def test_top_p_alone_is_allowed_when_top_k_is_zero() -> None:
    request = params.normalize_params("music", "x", 5, top_k=0, top_p=0.9)
    assert request.top_k == 0 and request.top_p == 0.9


def test_resolve_seed() -> None:
    assert params.resolve_seed(7) == 7
    assert params.resolve_seed(0) == 0
    first = params.resolve_seed(params.SEED_RANDOM, random.Random(1))
    assert first == params.resolve_seed(params.SEED_RANDOM, random.Random(1))
    assert 0 <= first <= params.SEED_MAX
    assert 0 <= params.resolve_seed(params.SEED_RANDOM) <= params.SEED_MAX


def test_output_stem_and_paths_and_unique_stem(tmp_path: Path) -> None:
    stem = params.build_output_stem("music", 123, NOW)
    assert stem == "20261027-153012-music-123"
    wav_path, meta_path = params.output_paths(tmp_path, stem)
    assert (wav_path.name, meta_path.name) == (f"{stem}.wav", f"{stem}.json")
    assert params.unique_stem(tmp_path, stem) == stem
    wav_path.write_bytes(b"")
    assert params.unique_stem(tmp_path, stem) == f"{stem}-2"
    (tmp_path / f"{stem}-2.json").write_text("{}", encoding="utf-8")
    assert params.unique_stem(tmp_path, stem) == f"{stem}-3"


def _metadata(tmp_path: Path, *, stem: str = "a", prompt: str = "calm piano", now: dt.datetime = NOW) -> dict:
    request = params.normalize_params("music", prompt, 10)
    return params.build_metadata(
        request,
        resolved_seed=99,
        model_id="facebook/musicgen-medium",
        sample_rate=32000,
        samples=320000,
        elapsed_sec=12.3456,
        peak_vram_mib=4321.06,
        device="cuda",
        wav_name=f"{stem}.wav",
        now=now,
    )


def test_build_metadata_is_json_serializable_and_complete(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path)
    assert json.loads(json.dumps(metadata)) == metadata
    assert metadata["duration_actual_sec"] == 10.0
    assert metadata["elapsed_sec"] == 12.35 and metadata["peak_vram_mib"] == 4321.1
    assert metadata["created_at"] == "2026-10-27T15:30:12"
    assert metadata["seed"] == 99 and metadata["model"] == "facebook/musicgen-medium"


def test_build_metadata_without_cuda_has_no_vram_value() -> None:
    request = params.normalize_params("sound", "dog barking", 5)
    metadata = params.build_metadata(
        request, resolved_seed=1, model_id="m", sample_rate=16000, samples=80000, elapsed_sec=1.0,
        peak_vram_mib=None, device="cpu", wav_name="x.wav", now=NOW,
    )
    assert metadata["peak_vram_mib"] is None and metadata["device"] == "cpu"


def test_list_recent_outputs_newest_first_and_skips_broken(tmp_path: Path) -> None:
    assert params.list_recent_outputs(tmp_path / "nothing") == []
    for index, stem in enumerate(("20261027-100000-music-1", "20261027-110000-music-2", "20261027-120000-music-3")):
        (tmp_path / f"{stem}.wav").write_bytes(b"")
        meta = _metadata(tmp_path, stem=stem, prompt=f"prompt {index}")
        (tmp_path / f"{stem}.json").write_text(json.dumps(meta), encoding="utf-8")
    (tmp_path / "20261027-130000-music-4.json").write_text("{ broken", encoding="utf-8")  # 壊れた json
    orphan = _metadata(tmp_path, stem="20261027-140000-music-5")
    (tmp_path / "20261027-140000-music-5.json").write_text(json.dumps(orphan), encoding="utf-8")  # wav が無い
    rows = params.list_recent_outputs(tmp_path)
    assert [row["prompt"] for row in rows] == ["prompt 2", "prompt 1", "prompt 0"]
    assert len(params.list_recent_outputs(tmp_path, limit=2)) == 2


# --------------------------------------------------------------------------
# help_texts
# --------------------------------------------------------------------------


def test_help_texts_state_the_noncommercial_license_and_english_prompts() -> None:
    assert "CC-BY-NC 4.0" in help_texts.LICENSE_MD and "非商用" in help_texts.LICENSE_MD
    assert "英語" in help_texts.PROMPT_TIPS_MD


def test_help_texts_durations_and_rates_match_params() -> None:
    music = params.KIND_SPECS[params.KIND_MUSIC]
    sound = params.KIND_SPECS[params.KIND_SOUND]
    assert f"1〜{music.max_duration:g} 秒" in help_texts.KIND_INTRO[params.KIND_MUSIC]
    assert f"{music.sample_rate // 1000} kHz" in help_texts.KIND_INTRO[params.KIND_MUSIC]
    assert f"1〜{sound.max_duration:g} 秒" in help_texts.KIND_INTRO[params.KIND_SOUND]
    assert f"{sound.sample_rate // 1000} kHz" in help_texts.KIND_INTRO[params.KIND_SOUND]


def test_help_text_examples_pass_validation() -> None:
    for kind, prompts in help_texts.KIND_EXAMPLES.items():
        for prompt in prompts:
            params.normalize_params(kind, prompt, params.KIND_SPECS[kind].default_duration)


def test_format_metadata_text_mentions_key_facts(tmp_path: Path) -> None:
    text = help_texts.format_metadata_text(_metadata(tmp_path))
    for fragment in ("作曲", "calm piano", "seed: 99", "12.35 秒", "4321.1 MiB", "a.wav"):
        assert fragment in text


# --------------------------------------------------------------------------
# cli（純粋関数）
# --------------------------------------------------------------------------


def test_build_runtime_env_does_not_mutate_base_and_overrides_cache_dirs() -> None:
    base = {"PATH": "/usr/bin", "HF_HOME": "/somewhere/else"}
    env = cli.build_runtime_env(base)
    assert base == {"PATH": "/usr/bin", "HF_HOME": "/somewhere/else"}
    assert env["PATH"] == "/usr/bin" and env["HF_HOME"] == str(config.HF_HOME_DIR)
    assert env["AUDIOCRAFT_CACHE_DIR"] == str(config.AUDIOCRAFT_CACHE_DIR)


def test_build_serve_command() -> None:
    assert cli.build_serve_command("/v/python", "0.0.0.0", 7862, None) == [
        "/v/python", "-m", "acraft.ui", "--host", "0.0.0.0", "--port", "7862"
    ]
    assert cli.build_serve_command("/v/python", "127.0.0.1", 1, "cpu")[-2:] == ["--device", "cpu"]


def test_classify_port_status() -> None:
    assert cli.classify_port_status(7862, False, False)[0] is True
    assert cli.classify_port_status(7862, True, True)[0] is True
    assert cli.classify_port_status(7862, True, False)[0] is False


def test_classify_vram_status() -> None:
    assert cli.classify_vram_status(None, 1024, False)[0] is False
    assert cli.classify_vram_status(100, 1024, False)[0] is True
    assert cli.classify_vram_status(20000, 1024, True)[0] is True
    ok, detail = cli.classify_vram_status(20000, 1024, False)
    assert ok is False and "入口ページ" in detail


GOOD_PROBE = {
    "torch_version": "2.1.0+cu121", "cuda_version": "12.1", "cuda_available": True, "device_name": "NVIDIA A30",
    "xformers_version": "0.0.22.post7", "soundfile_version": "0.14.0", "spacy_version": "3.7.6",
    "transformers_version": "4.46.0", "gradio_version": "5.33.0", "audiocraft_version": "1.4.0a2",
}


def test_evaluate_probe_all_ok() -> None:
    rows = cli.evaluate_probe(GOOD_PROBE, skip_gpu=False)
    assert all(ok for _, ok, _ in rows), [row for row in rows if not row[1]]


def test_evaluate_probe_skip_gpu_does_not_require_cuda() -> None:
    probe = {key: value for key, value in GOOD_PROBE.items() if key not in ("cuda_available", "device_name")}
    rows = cli.evaluate_probe(probe, skip_gpu=True)
    assert all(ok for _, ok, _ in rows)
    assert not any("is_available" in label for label, _, _ in rows)


@pytest.mark.parametrize(
    "changes, label_fragment",
    [
        ({"torch_version": "2.6.0+cu126", "cuda_version": "12.6"}, "torch が 2.1.0 系"),
        ({"cuda_version": "12.6"}, "torch.version.cuda"),
        ({"cuda_available": False}, "is_available"),
        ({"device_name": "NVIDIA RTX"}, "A30"),
        ({"xformers_version": "0.0.30"}, "xformers が 0.0.22 系"),
        ({"xformers_error": "ImportError: x"}, "xformers の import"),
        ({"audiocraft_error": "ModuleNotFoundError: demucs"}, "audiocraft の import"),
        ({"soundfile_error": "OSError: libsndfile"}, "soundfile の import"),
    ],
)
def test_evaluate_probe_flags_problems(changes, label_fragment) -> None:
    probe = {**GOOD_PROBE, **changes}
    bad = [label for label, ok, _ in cli.evaluate_probe(probe, skip_gpu=False) if not ok]
    assert any(label_fragment in label for label in bad), (bad, changes)


def test_evaluate_probe_reports_torch_import_failure_only_once() -> None:
    rows = cli.evaluate_probe({"torch_error": "ImportError: no torch"}, skip_gpu=False)
    assert rows[0] == ("torch の import", False, "ImportError: no torch")
    assert not any("cuda" in label.lower() for label, _, _ in rows if label.startswith("torch"))


def test_hub_cache_dir_name() -> None:
    assert cli.hub_cache_dir_name("facebook/musicgen-medium") == "models--facebook--musicgen-medium"


def test_find_weight_files_and_summary(tmp_path: Path) -> None:
    assert cli.find_weight_files(tmp_path, "facebook/musicgen-medium") == []
    snapshot = tmp_path / "models--facebook--musicgen-medium" / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "state_dict.bin").write_bytes(b"x")
    found = cli.find_weight_files(tmp_path, "facebook/musicgen-medium")
    assert found == ["state_dict.bin"]
    ok, detail = cli.summarize_weights(found)
    assert ok is False and "compression_state_dict.bin" in detail
    (snapshot / "compression_state_dict.bin").write_bytes(b"x")
    assert cli.summarize_weights(cli.find_weight_files(tmp_path, "facebook/musicgen-medium"))[0] is True


def test_find_weight_files_ignores_dangling_symlink(tmp_path: Path) -> None:
    snapshot = tmp_path / "models--facebook--audiogen-medium" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "state_dict.bin").symlink_to(tmp_path / "missing-blob")
    assert cli.find_weight_files(tmp_path, "facebook/audiogen-medium") == []


def test_text_encoder_present(tmp_path: Path) -> None:
    assert cli.text_encoder_present(tmp_path) is False
    snapshot = tmp_path / "hub" / "models--google-t5--t5-base" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "spiece.model").write_bytes(b"x")
    assert cli.text_encoder_present(tmp_path) is False  # 重みがまだ
    (snapshot / "model.safetensors").write_bytes(b"x")
    assert cli.text_encoder_present(tmp_path) is True


def test_generate_parser_defaults_and_choices() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["generate", "--kind", "sound", "--prompt", "dog barking"])
    assert args.device == "auto" and args.seed == params.SEED_RANDOM and args.duration is None
    with pytest.raises(SystemExit):
        parser.parse_args(["generate", "--kind", "video", "--prompt", "x"])


def test_generate_rejects_bad_params_before_loading_anything(capsys) -> None:
    # 検証に落ちたら torch を import せず終了コード 2（モデルの読み込みに数十秒かかるため、先に弾く）
    code = cli.main(["generate", "--kind", "music", "--prompt", "", "--duration", "5"])
    assert code == 2
    assert "説明文が空" in capsys.readouterr().err
    assert "torch" not in sys.modules


# --------------------------------------------------------------------------
# systemd ユニットの雛形・ファイル構成
# --------------------------------------------------------------------------

UNIT_TEXT = (TOOL_DIR / "systemd" / "audiocraft.service.example").read_text(encoding="utf-8")


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
    portal_keys = re.findall(r'"([^"]+)"', match.group(1))
    assert set(portal_keys) == set(cli.GPU_CONFLICT_UNITS) | {"audiocraft"}


def test_unit_template_uses_placeholder_and_standard_layout() -> None:
    assert "@REPO_ROOT@/tools/audiocraft/run-audiocraft serve" in _unit_line("ExecStart=")
    assert _unit_line("WorkingDirectory=") == "WorkingDirectory=@REPO_ROOT@/tools/audiocraft"
    assert _unit_line("EnvironmentFile=") == "EnvironmentFile=-%h/.config/audiocraft/audiocraft.env"
    assert _unit_line("MemoryHigh=") == "MemoryHigh=16G" and _unit_line("MemoryMax=") == "MemoryMax=20G"
    assert "7862" in UNIT_TEXT.splitlines()[1]
    assert not re.search(r"/home/|192\.168\.", UNIT_TEXT)


def test_upstream_pin_matches_setup_script_and_readme() -> None:
    setup_text = (TOOL_DIR / "setup-server.sh").read_text(encoding="utf-8")
    assert f'UPSTREAM_URL="{config.UPSTREAM_URL}"' in setup_text
    assert f'UPSTREAM_COMMIT="{config.UPSTREAM_COMMIT}"' in setup_text
    assert config.UPSTREAM_COMMIT in (TOOL_DIR / "requirements.lock.txt").read_text(encoding="utf-8")


def test_lock_file_pins_the_old_stack_audiocraft_requires() -> None:
    lock = (TOOL_DIR / "requirements.lock.txt").read_text(encoding="utf-8")
    pins = dict(
        line.split("==", 1) for line in lock.splitlines() if line and not line.startswith("#") and "==" in line
    )
    assert pins["xformers"] == "0.0.22.post7" and pins["torchtext"] == "0.16.0"
    assert pins["numpy"].startswith("1.")  # numpy 2 系は上流が <2.0.0 で止めている
    assert "torch" not in pins  # torch 系は cu121 の index から setup-server.sh が入れる


def test_run_script_is_executable_bash() -> None:
    script = TOOL_DIR / "run-audiocraft"
    assert script.stat().st_mode & 0o111
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0


def test_python_sources_compile_without_importing_heavy_modules() -> None:
    # engine.py / ui.py は torch・gradio、rest.py は fastapi が要るので、ここでは import せず構文だけ確かめる
    # （rest.py の中身は tests/test_rest.py が確かめる）
    for name in ("engine.py", "ui.py", "rest.py"):
        source = (TOOL_DIR / "acraft" / name).read_text(encoding="utf-8")
        compile(source, name, "exec")


@pytest.mark.parametrize("skip_gpu", (True, False))
def test_env_probe_script_formats_to_valid_python(skip_gpu: bool) -> None:
    # {skip_gpu} 以外の波括弧を {{ }} で書き忘れると、venv の python で動かす前に KeyError になる
    script = cli._ENV_PROBE.format(skip_gpu=skip_gpu)
    compile(script, "_ENV_PROBE", "exec")
    assert f"SKIP_GPU = {skip_gpu}" in script
    assert "from audiocraft.models import AudioGen, MusicGen" in script


# --------------------------------------------------------------------------
# REST の口の純粋関数（parse_rest_body・build_response_headers）
# --------------------------------------------------------------------------


def test_parse_rest_body_applies_the_per_kind_default_duration() -> None:
    music = params.parse_rest_body("music", {"prompt": "calm piano"})
    sound = params.parse_rest_body("sound", {"prompt": "dog barking"})
    assert (music.params.duration, sound.params.duration) == (10.0, 5.0)
    assert music.keep is True and music.params.seed == params.SEED_RANDOM


def test_parse_rest_body_accepts_every_documented_field() -> None:
    body = {"prompt": "x", "duration": 7, "seed": 3, "top_k": 100, "top_p": 0.5, "temperature": 0.8, "cfg_coef": 4.0, "keep": False}
    request = params.parse_rest_body("music", body)
    assert request.keep is False
    assert (request.params.duration, request.params.seed, request.params.top_k) == (7.0, 3, 100)
    assert (request.params.top_p, request.params.temperature, request.params.cfg_coef) == (0.5, 0.8, 4.0)
    assert set(params.REST_BODY_FIELDS) == set(body)


def test_parse_rest_body_null_means_default_but_false_keep_is_kept() -> None:
    request = params.parse_rest_body("sound", {"prompt": "x", "duration": None, "seed": None, "keep": None})
    assert request.params.duration == 5.0 and request.keep is True


@pytest.mark.parametrize(
    "kind, body, fragment",
    [
        ("video", {"prompt": "x"}, "種類"),
        ("music", None, "オブジェクト"),
        ("music", {"prompt": "x", "kind": "sound"}, "未知の項目があります: kind"),
        ("music", {"prompt": "x", "keep": 1}, "keep は true か false"),
        ("music", {}, "prompt"),
    ],
)
def test_parse_rest_body_rejects_with_reason(kind, body, fragment) -> None:
    with pytest.raises(params.ParamError) as raised:
        params.parse_rest_body(kind, body)
    assert fragment in str(raised.value)


def test_build_response_headers(tmp_path: Path) -> None:
    metadata = _metadata(tmp_path, stem="20261027-153012-music-99")
    headers = params.build_response_headers(metadata, keep=True)
    assert headers["X-Acraft-Seed"] == "99" and headers["X-Acraft-Sample-Rate"] == "32000"
    assert headers["X-Acraft-Duration-Sec"] == "10.0" and headers["X-Acraft-Elapsed-Sec"] == "12.35"
    assert headers["X-Acraft-File"] == "20261027-153012-music-99.wav"
    assert headers["Content-Disposition"] == 'attachment; filename="20261027-153012-music-99.wav"'
    assert "X-Acraft-File" not in params.build_response_headers(metadata, keep=False)
    assert all(value.isascii() for value in headers.values())  # HTTP ヘッダは ASCII だけ


def test_help_texts_api_section_matches_the_real_endpoints() -> None:
    api_text = help_texts.API_MD
    for fragment in ("/api/v1/music", "/api/v1/sound", "/api/v1/health", f":{config.PORT}", "application/json", "415", "503", "keep"):
        assert fragment in api_text, fragment
    for name in params.REST_BODY_FIELDS:
        assert f"`{name}`" in api_text, name
