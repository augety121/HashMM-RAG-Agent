"""Tests for config + bit-packing math.

Bit packing is tested against numpy.packbits which is the reference Faiss
uses. This runs even on a torch-less machine.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from hashmm.config import HashMMConfig


def test_config_defaults_smoke(monkeypatch):
    """Config instantiates with all defaults and creates dirs."""
    with tempfile.TemporaryDirectory() as td:
        cfg = HashMMConfig(
            working_dir=str(Path(td) / "rag"),
            parser_output_dir=str(Path(td) / "out"),
            hash_index_dir=str(Path(td) / "idx"),
            checkpoint_dir=str(Path(td) / "ckpt"),
            data_dir=str(Path(td) / "data"),
        )
        for d in (cfg.working_dir, cfg.parser_output_dir, cfg.hash_index_dir,
                  cfg.checkpoint_dir, cfg.data_dir):
            assert Path(d).is_dir()
        assert cfg.hash_bits in (32, 64, 128, 256, 512, 1024)


def test_config_rejects_bad_bits():
    with pytest.raises(ValueError):
        HashMMConfig(hash_bits=100)


def test_config_rejects_bad_hybrid_mode():
    with pytest.raises(ValueError):
        HashMMConfig(hybrid_mode="invalid")


def test_config_to_dict_redacts_api_key():
    cfg = HashMMConfig(llm_api_key="sk-real-secret-here")
    d = cfg.to_dict()
    assert d["llm_api_key"] != "sk-real-secret-here"
    assert "redacted" in d["llm_api_key"].lower()


# ── Pure-numpy bit packing mirror, for verification ────────────────────


def _np_pack_bits(codes: np.ndarray) -> np.ndarray:
    K = codes.shape[1]
    assert K % 8 == 0
    bits = (codes > 0).astype(np.uint8)
    weights = np.array([1, 2, 4, 8, 16, 32, 64, 128], dtype=np.uint8)
    return (bits.reshape(bits.shape[0], -1, 8) * weights).sum(axis=-1).astype(np.uint8)


def test_pack_matches_numpy_packbits():
    """Our packing is bit-equivalent to np.packbits(bitorder='little')."""
    rng = np.random.default_rng(0)
    codes = rng.choice([-1.0, 1.0], size=(8, 128)).astype(np.float32)
    ours = _np_pack_bits(codes)
    ref = np.packbits((codes > 0).astype(np.uint8), axis=1, bitorder="little")
    assert np.array_equal(ours, ref)


def test_pack_rejects_non_multiple_of_8():
    with pytest.raises(AssertionError):
        _np_pack_bits(np.ones((1, 100), dtype=np.float32))  # 100 not multiple of 8


def test_hamming_arithmetic():
    """popcount(XOR) → hamming distance."""
    a = np.array([[0b11110000]], dtype=np.uint8)
    b = np.array([[0b10100110]], dtype=np.uint8)
    xor = np.bitwise_xor(a, b)
    table = np.array([bin(i).count("1") for i in range(256)], dtype=np.int32)
    assert int(table[xor].sum()) == 4  # bits differ: positions 0,2,4,5
