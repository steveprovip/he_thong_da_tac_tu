#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_llm_client.py - profile resolution, VRAM arithmetic, persona multiplexing.

    pytest tests/test_llm_client.py --budget-cap 10000

No llama-server runs during this suite. `LocalLLMClient` takes an injectable
transport precisely so the message assembly, persona parameters, and JSON
salvaging can be tested without a 9 GB CUDA process, and the profile tests build
their own config files in tmp_path rather than depending on which GGUFs happen to
be on the machine.

What is being defended
----------------------
1. Measured file size beats the documented claim. BRAIN.md lists Qwen3.8-27B
   IQ2_XXS at 7.27 GB; the file on this machine is 8.39 GB. A registry that
   trusted the document would understate Profile S VRAM by over a gigabyte, so
   `resolve()` stats the file and warns when the two disagree.
2. A missing model reports where it looked. "Model not found" without the search
   path is an unactionable error.
3. Two profiles are never declared co-resident. 16 GB cannot hold two of these.
4. The CUDA overhead constant is an estimate and is labelled as one everywhere it
   surfaces, because 0.55 GB is a figure BRAIN.md itself flags as uncalibrated.
5. Remote fallback is off unless explicitly enabled, so a local-only run cannot
   silently egress prompt content to a paid API.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any, Optional

import pytest

from local_llm_client import (
    DEFAULT_CONFIG,
    PERSONA_PARAMS,
    PERSONA_PROMPTS,
    LLMError,
    LocalLLMClient,
    ModelNotFoundError,
    Persona,
    ProfileRegistry,
    VramBudgetError,
    estimate_kv_cache_gb,
    estimate_vram_gb,
)


# ---------------------------------------------------------------------------
# Config-file helpers & zero-overhead virtual file sizing
# ---------------------------------------------------------------------------

_orig_stat = Path.stat


def _mock_stat(self, *args, **kwargs):
    st = _orig_stat(self, *args, **kwargs)
    sf = self.with_name(self.name + ".mock_size")
    if sf.exists():
        try:
            fake = int(sf.read_text().strip())
            return os.stat_result(
                (
                    st.st_mode,
                    st.st_ino,
                    st.st_dev,
                    st.st_nlink,
                    st.st_uid,
                    st.st_gid,
                    fake,
                    st.st_atime,
                    st.st_mtime,
                    st.st_ctime,
                )
            )
        except Exception:
            pass
    return st


Path.stat = _mock_stat


def write_config(
    tmp_path: Path,
    *,
    model_bytes: Optional[int] = None,
    disk_gb_claimed: float = 8.0,
    context: int = 8192,
    kv_type: str = "q4_0",
    extra_profiles: Optional[dict[str, Any]] = None,
    search_paths: Optional[list[str]] = None,
    fallback_enabled: bool = False,
    default_profile: str = "S",
) -> Path:
    """Build a minimal but structurally real profiles.json under tmp_path."""
    models = tmp_path / "models"
    models.mkdir(exist_ok=True)
    model_path: Optional[Path] = None
    if model_bytes is not None:
        model_path = models / "test-model.gguf"
        model_path.write_bytes(b"")
        (models / "test-model.gguf.mock_size").write_text(str(model_bytes))

    profiles: dict[str, Any] = {
        "S": {
            "label": "test speed profile",
            "model_file": "test-model.gguf",
            "explicit_path": str(model_path) if model_path else None,
            "quant": "IQ2_XXS",
            "disk_gb_claimed": disk_gb_claimed,
            "vram_gb_claimed": 8.0,
            "context": context,
            "kv_type": kv_type,
            "kv_kib_per_token_f16": 64.0,
            "mtp": False,
            "confidence": "INFERRED",
            "reasoning_effort": "medium",
        }
    }
    if extra_profiles:
        profiles.update(extra_profiles)

    cfg = {
        "schema_version": 2,
        "budget": {
            "vram_ceiling_gb": 10.0,
            "ram_ceiling_gb": 6.0,
            "cuda_overhead_gb": 0.55,
            "tolerance_gb": 0.75,
        },
        "search_paths": search_paths if search_paths is not None else [str(models)],
        "profiles": profiles,
        "default_profile": default_profile,
        "server": {"host": "127.0.0.1", "port": 8080, "request_timeout_s": 30},
        "fallback": {"enabled": fallback_enabled, "provider": "anthropic",
                     "model": "x", "api_key_env": "NOPE"},
    }
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


GB = 1024 ** 3


def fake_transport(reply: str = "hello", *, usage: Optional[dict] = None):
    """A transport that records payloads and returns an OpenAI-shaped response."""
    sent: list[dict[str, Any]] = []

    def transport(path: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        sent.append({"path": path, "payload": payload, "timeout_s": timeout_s})
        return {
            "choices": [{"message": {"role": "assistant", "content": reply},
                         "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 11, "completion_tokens": 5,
                               "total_tokens": 16},
        }

    transport.sent = sent  # type: ignore[attr-defined]
    return transport


# ---------------------------------------------------------------------------
# VRAM arithmetic
# ---------------------------------------------------------------------------


class TestVramArithmetic:
    def test_kv_scales_linearly_with_context(self):
        a = estimate_kv_cache_gb(context=8192, kib_per_token_f16=64.0, kv_type="f16")
        b = estimate_kv_cache_gb(context=16384, kib_per_token_f16=64.0, kv_type="f16")
        assert abs(b - 2 * a) < 1e-9

    def test_quantized_kv_is_smaller(self):
        f16 = estimate_kv_cache_gb(context=8192, kib_per_token_f16=64.0, kv_type="f16")
        q8 = estimate_kv_cache_gb(context=8192, kib_per_token_f16=64.0, kv_type="q8_0")
        q4 = estimate_kv_cache_gb(context=8192, kib_per_token_f16=64.0, kv_type="q4_0")
        assert q4 < q8 < f16

    def test_kv_known_value(self):
        # 8192 tokens * 64 KiB = 512 MiB at f16 = 0.5 GiB exactly.
        got = estimate_kv_cache_gb(context=8192, kib_per_token_f16=64.0, kv_type="f16")
        assert abs(got - 0.5) < 1e-6

    def test_total_is_the_sum_of_parts(self):
        est = estimate_vram_gb(weights_gb=8.0, context=8192,
                               kib_per_token_f16=64.0, kv_type="q4_0",
                               overhead_gb=0.55)
        assert abs(est.total_gb - (est.weights_gb + est.kv_cache_gb + est.overhead_gb)) < 1e-9

    def test_overhead_is_flagged_as_an_estimate(self):
        est = estimate_vram_gb(weights_gb=8.0, context=8192,
                               kib_per_token_f16=64.0, kv_type="q4_0",
                               overhead_gb=0.55)
        blob = json.dumps(est.as_dict()).lower()
        assert "estimate" in blob, (
            "BRAIN.md flags 0.55 GB as uncalibrated; the label must travel with it"
        )

    def test_bigger_context_costs_more_vram(self):
        small = estimate_vram_gb(weights_gb=8.0, context=8192,
                                 kib_per_token_f16=64.0, kv_type="q4_0")
        big = estimate_vram_gb(weights_gb=8.0, context=94208,
                               kib_per_token_f16=64.0, kv_type="q4_0")
        assert big.total_gb > small.total_gb

    def test_unknown_kv_type_is_not_silently_free(self):
        got = estimate_kv_cache_gb(context=8192, kib_per_token_f16=64.0,
                                   kv_type="not-a-real-type")
        assert got > 0.0, "an unrecognised kv type must not compute to zero cost"


# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------


class TestProfileResolution:
    def test_present_model_resolves(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8.39 * GB), disk_gb_claimed=8.39)
        r = ProfileRegistry(cfg).resolve("S")
        assert r.found is True
        assert r.model_path is not None
        assert Path(r.model_path).exists()

    def test_measured_size_wins_over_the_claim(self, tmp_path):
        """The BRAIN.md discrepancy, in miniature.

        Claim says 7.27 GB, the file is 8.39 GB. The resolved profile must carry
        the measured number, because that is what actually loads into VRAM.
        """
        cfg = write_config(tmp_path, model_bytes=int(8.39 * GB), disk_gb_claimed=7.27)
        r = ProfileRegistry(cfg).resolve("S")
        assert abs(r.disk_gb_measured - 8.39) < 0.05
        assert abs(r.vram.weights_gb - 8.39) < 0.05, "VRAM must use the measured size"

    def test_size_drift_warns_with_both_numbers(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8.39 * GB), disk_gb_claimed=7.27)
        r = ProfileRegistry(cfg).resolve("S")
        blob = " ".join(r.warnings)
        assert "7.27" in blob and "8.39" in blob, (
            f"a drift warning must name the claim and the measurement: {r.warnings}"
        )

    def test_matching_size_does_not_warn_about_drift(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8.0 * GB), disk_gb_claimed=8.0)
        r = ProfileRegistry(cfg).resolve("S")
        assert not any("disk" in w.lower() for w in r.warnings), r.warnings

    def test_absent_model_reports_where_it_looked(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=None,
                           search_paths=[str(tmp_path / "nowhere")])
        r = ProfileRegistry(cfg).resolve("S")
        assert r.found is False
        assert r.searched, "an unactionable 'not found' is a bug"
        assert any("nowhere" in str(p) for p in r.searched)

    def test_require_raises_on_absent_model(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=None,
                           search_paths=[str(tmp_path / "nowhere")])
        with pytest.raises(ModelNotFoundError):
            ProfileRegistry(cfg).require("S")

    def test_unknown_profile_key_raises(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(1 * GB))
        with pytest.raises((KeyError, ValueError)):
            ProfileRegistry(cfg).resolve("ZZZ")

    def test_missing_config_file_raises(self, tmp_path):
        with pytest.raises((FileNotFoundError, OSError)):
            ProfileRegistry(tmp_path / "does-not-exist.json")

    def test_low_confidence_is_surfaced(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        r = ProfileRegistry(cfg).resolve("S")
        blob = (" ".join(r.warnings) + json.dumps(r.as_dict())).lower()
        assert "inferred" in blob, "an unverified throughput figure must say so"

    def test_over_ceiling_profile_is_flagged(self, tmp_path):
        """A profile whose projected VRAM exceeds the ceiling must not look healthy."""
        cfg = write_config(tmp_path, model_bytes=int(20 * GB), disk_gb_claimed=20.0)
        reg = ProfileRegistry(cfg)
        r = reg.resolve("S")
        assert r.vram.total_gb > 14.0
        assert r.over_budget is True
        with pytest.raises(VramBudgetError):
            reg.require("S")

    def test_resolve_all_covers_every_profile(self, tmp_path):
        extra = {
            "Q": {
                "label": "second", "model_file": "other.gguf", "explicit_path": None,
                "quant": "IQ3_XXS", "disk_gb_claimed": 10.9, "vram_gb_claimed": 13.07,
                "context": 32768, "kv_type": "q4_0", "kv_kib_per_token_f16": 64.0,
                "mtp": True, "confidence": "MEASURED", "reasoning_effort": "medium",
            }
        }
        cfg = write_config(tmp_path, model_bytes=int(8 * GB), extra_profiles=extra)
        assert set(ProfileRegistry(cfg).resolve_all()) == {"S", "Q"}

    def test_real_repo_config_parses(self, config_path):
        """The shipped profiles.json must load even where no weights exist."""
        if not config_path.exists():
            pytest.skip("config/profiles.json not present")
        reg = ProfileRegistry(config_path)
        resolved = reg.resolve_all()
        assert set(resolved) >= {"S", "Q", "T"}
        assert reg.default_profile in resolved


# ---------------------------------------------------------------------------
# Single residency
# ---------------------------------------------------------------------------


class TestSingleResidency:
    def test_one_profile_is_fine(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        ProfileRegistry(cfg).ensure_single_resident(["S"])  # must not raise

    def test_two_profiles_rejected(self, tmp_path):
        extra = {
            "Q": {
                "label": "second", "model_file": "other.gguf", "explicit_path": None,
                "quant": "IQ3_XXS", "disk_gb_claimed": 10.9, "vram_gb_claimed": 13.07,
                "context": 32768, "kv_type": "q4_0", "kv_kib_per_token_f16": 64.0,
                "mtp": True, "confidence": "MEASURED", "reasoning_effort": "medium",
            }
        }
        cfg = write_config(tmp_path, model_bytes=int(8 * GB), extra_profiles=extra)
        with pytest.raises(VramBudgetError):
            ProfileRegistry(cfg).ensure_single_resident(["S", "Q"])

    def test_empty_set_is_fine(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        ProfileRegistry(cfg).ensure_single_resident([])


# ---------------------------------------------------------------------------
# Launch command
# ---------------------------------------------------------------------------


class TestLaunchCommand:
    def test_command_has_the_essentials(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB), context=8192)
        argv = ProfileRegistry(cfg).launch_command("S")
        joined = " ".join(argv)
        assert "-m" in argv
        assert "test-model.gguf" in joined
        assert "8192" in joined

    def test_parallel_one_because_one_model_is_resident(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        joined = " ".join(ProfileRegistry(cfg).launch_command("S"))
        assert "--parallel 1" in joined or "--parallel" in joined

    def test_no_mtp_flag_for_a_two_bit_quant(self, tmp_path):
        """BRAIN.md QD-3: at 2-3 bit, MTP makes generation slower."""
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        joined = " ".join(ProfileRegistry(cfg).launch_command("S"))
        assert "draft" not in joined.lower()

    def test_missing_model_cannot_be_launched(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=None,
                           search_paths=[str(tmp_path / "nowhere")])
        with pytest.raises(ModelNotFoundError):
            ProfileRegistry(cfg).launch_command("S")


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------


class TestPersonas:
    def test_five_roles_exist(self):
        assert {p.value for p in Persona} == {
            "orchestrator", "researcher", "designer", "copywriter", "sentry"
        }

    def test_every_persona_has_a_prompt_and_params(self):
        for p in Persona:
            assert PERSONA_PROMPTS.get(p), p
            params = PERSONA_PARAMS.get(p)
            assert params and {"temperature", "top_p", "max_tokens"} <= set(params), p

    def test_sentry_is_deterministic(self):
        """The auditor must not sample. Same input, same verdict."""
        assert PERSONA_PARAMS[Persona.SENTRY]["temperature"] == 0.0

    def test_creative_roles_are_warmer_than_the_sentry(self):
        assert (PERSONA_PARAMS[Persona.COPYWRITER]["temperature"]
                > PERSONA_PARAMS[Persona.SENTRY]["temperature"])

    def test_sentry_prompt_denies_when_uncertain(self):
        assert "DENY" in PERSONA_PROMPTS[Persona.SENTRY]

    def test_orchestrator_prompt_states_the_serial_constraint(self):
        text = PERSONA_PROMPTS[Persona.ORCHESTRATOR].lower()
        assert "serial" in text or "one model" in text

    def test_system_prompt_is_sent(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        t = fake_transport("ok")
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=t)
        client.complete(Persona.DESIGNER, "make a logo")
        msgs = t.sent[0]["payload"]["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == PERSONA_PROMPTS[Persona.DESIGNER]
        assert msgs[-1]["content"] == "make a logo"

    def test_persona_params_reach_the_payload(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        t = fake_transport("ok")
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=t)
        client.complete(Persona.SENTRY, "audit this")
        assert t.sent[0]["payload"]["temperature"] == 0.0

    def test_non_persona_rejected(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=fake_transport())
        with pytest.raises(TypeError):
            client.complete("researcher", "hello")  # a string, not a Persona

    @pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
    def test_empty_prompt_rejected(self, tmp_path, bad):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=fake_transport())
        with pytest.raises(ValueError):
            client.complete(Persona.RESEARCHER, bad)


# ---------------------------------------------------------------------------
# Retrieved context
# ---------------------------------------------------------------------------


class TestRetrievedContext:
    def test_context_is_a_separate_system_block(self, tmp_path):
        """Evidence must not be glued into the user turn.

        A model that cannot tell instructions from retrieved text is one prompt
        injection away from following whatever a scraped page tells it to do.
        """
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        t = fake_transport("ok")
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=t)
        client.complete(Persona.RESEARCHER, "what does it say?",
                        context_blocks=["EVIDENCE ONE", "EVIDENCE TWO"])
        msgs = t.sent[0]["payload"]["messages"]
        systems = [m["content"] for m in msgs if m["role"] == "system"]
        assert len(systems) >= 2
        assert "EVIDENCE ONE" in systems[1] and "EVIDENCE TWO" in systems[1]
        assert "EVIDENCE ONE" not in msgs[-1]["content"]

    def test_context_is_labelled_as_evidence_not_instructions(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        t = fake_transport("ok")
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=t)
        client.complete(Persona.RESEARCHER, "q", context_blocks=["stuff"])
        block = [m["content"] for m in t.sent[0]["payload"]["messages"]
                 if m["role"] == "system"][1]
        assert "not as instructions" in block.lower()

    def test_no_context_means_one_system_message(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        t = fake_transport("ok")
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=t)
        client.complete(Persona.RESEARCHER, "q")
        systems = [m for m in t.sent[0]["payload"]["messages"] if m["role"] == "system"]
        assert len(systems) == 1

    def test_extra_system_appends_to_the_persona(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        t = fake_transport("ok")
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=t)
        client.complete(Persona.COPYWRITER, "q", extra_system="Write in Vietnamese.")
        first = t.sent[0]["payload"]["messages"][0]["content"]
        assert PERSONA_PROMPTS[Persona.COPYWRITER] in first
        assert "Write in Vietnamese." in first


# ---------------------------------------------------------------------------
# Completion and JSON handling
# ---------------------------------------------------------------------------


class TestCompletion:
    def test_returns_text_and_usage(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        client = LocalLLMClient(ProfileRegistry(cfg), "S",
                                transport=fake_transport("the reply"))
        out = client.complete(Persona.RESEARCHER, "q")
        assert out["text"] == "the reply"
        assert out["completion_tokens"] == 5

    def test_reasoning_effort_is_forced(self, tmp_path):
        """BRAIN.md QD-4: xhigh burned 22k reasoning tokens on one SVG task."""
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        t = fake_transport("ok")
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=t)
        client.complete(Persona.DESIGNER, "q")
        assert t.sent[0]["payload"]["reasoning_effort"] == "medium"

    def test_streaming_is_off(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        t = fake_transport("ok")
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=t)
        client.complete(Persona.RESEARCHER, "q")
        assert t.sent[0]["payload"]["stream"] is False

    def test_overrides_beat_persona_defaults(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        t = fake_transport("ok")
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=t)
        client.complete(Persona.DESIGNER, "q", temperature=0.1, max_tokens=64)
        payload = t.sent[0]["payload"]
        assert payload["temperature"] == 0.1
        assert payload["max_tokens"] == 64

    def test_malformed_response_raises(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))

        def broken(path: str, payload: dict, timeout_s: float) -> dict:
            return {"unexpected": "shape"}

        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=broken)
        with pytest.raises(LLMError):
            client.complete(Persona.RESEARCHER, "q")

    def test_json_plain(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        client = LocalLLMClient(ProfileRegistry(cfg), "S",
                                transport=fake_transport('{"a": 1}'))
        assert client.complete_json(Persona.ORCHESTRATOR, "q") == {"a": 1}

    def test_json_in_a_fenced_block(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        client = LocalLLMClient(
            ProfileRegistry(cfg), "S",
            transport=fake_transport('```json\n{"a": 2}\n```'),
        )
        assert client.complete_json(Persona.ORCHESTRATOR, "q") == {"a": 2}

    def test_json_embedded_in_prose(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        client = LocalLLMClient(
            ProfileRegistry(cfg), "S",
            transport=fake_transport('Sure! Here you go: {"a": 3} Hope that helps.'),
        )
        assert client.complete_json(Persona.ORCHESTRATOR, "q")["a"] == 3

    def test_json_array_is_accepted(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        client = LocalLLMClient(ProfileRegistry(cfg), "S",
                                transport=fake_transport("[1, 2, 3]"))
        assert client.complete_json(Persona.ORCHESTRATOR, "q") == [1, 2, 3]

    def test_unsalvageable_json_raises_with_a_sample(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        client = LocalLLMClient(
            ProfileRegistry(cfg), "S",
            transport=fake_transport("I'd rather explain it in words."),
        )
        with pytest.raises(LLMError) as exc:
            client.complete_json(Persona.ORCHESTRATOR, "q")
        assert "rather explain" in str(exc.value), (
            "the error must show what the model actually said"
        )


# ---------------------------------------------------------------------------
# Server interaction and egress policy
# ---------------------------------------------------------------------------


class TestServerAndEgress:
    def test_health_is_false_with_no_server(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        # Port 1 with a short timeout: nothing listens, and it fails fast.
        client = LocalLLMClient(ProfileRegistry(cfg), "S",
                                base_url="http://127.0.0.1:1", timeout_s=0.5)
        assert client.health() is False

    def test_fallback_off_by_default(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB), fallback_enabled=False)
        prior = os.environ.pop("ENGINE_ALLOW_FALLBACK", None)
        try:
            client = LocalLLMClient(ProfileRegistry(cfg), "S",
                                    transport=fake_transport())
            assert client.allow_fallback is False, (
                "a local-only run must not silently egress prompts to a paid API"
            )
        finally:
            if prior is not None:
                os.environ["ENGINE_ALLOW_FALLBACK"] = prior

    def test_fallback_needs_an_explicit_opt_in(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB), fallback_enabled=True)
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=fake_transport())
        assert client.allow_fallback is True

    def test_env_var_can_enable_fallback(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB), fallback_enabled=False)
        prior = os.environ.get("ENGINE_ALLOW_FALLBACK")
        os.environ["ENGINE_ALLOW_FALLBACK"] = "1"
        try:
            client = LocalLLMClient(ProfileRegistry(cfg), "S",
                                    transport=fake_transport())
            assert client.allow_fallback is True
        finally:
            if prior is None:
                os.environ.pop("ENGINE_ALLOW_FALLBACK", None)
            else:
                os.environ["ENGINE_ALLOW_FALLBACK"] = prior

    def test_client_defaults_to_the_config_default_profile(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB), default_profile="S")
        client = LocalLLMClient(ProfileRegistry(cfg), transport=fake_transport())
        assert client.profile_key == "S"

    def test_switch_to_a_missing_profile_raises(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=fake_transport())
        with pytest.raises((ModelNotFoundError, KeyError, ValueError)):
            client.switch_profile("ZZZ")

    def test_endpoint_is_the_openai_chat_path(self, tmp_path):
        cfg = write_config(tmp_path, model_bytes=int(8 * GB))
        t = fake_transport("ok")
        client = LocalLLMClient(ProfileRegistry(cfg), "S", transport=t)
        client.complete(Persona.RESEARCHER, "q")
        assert t.sent[0]["path"] == "/v1/chat/completions"


# ---------------------------------------------------------------------------
# Storage policy
# ---------------------------------------------------------------------------


class TestStoragePolicy:
    def test_default_config_lives_in_the_repo(self, repo_root):
        p = Path(DEFAULT_CONFIG)
        assert "KyThuatNenAIMoi" not in str(p)
        assert p.is_relative_to(repo_root)

    def test_reference_workspace_is_read_only(self, config_path):
        """Reading weights from the reference workspace is allowed; writing is not."""
        if not config_path.exists():
            pytest.skip("config/profiles.json not present")
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        for key in ("state_dir", "log_dir", "output_dir", "cache_dir"):
            value = str(cfg.get(key, ""))
            assert "KyThuatNenAIMoi" not in value, (key, value)
