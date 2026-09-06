"""Shared pytest fixtures."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """Per-test SQLite DB path; auto-cleaned by tmp_path."""
    return str(tmp_path / "test_fetch.db")


@pytest.fixture(scope="session")
def tiny_mp4(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """1-second silent black-frame MP4 for whisper-normalize tests.
    Skips if ffmpeg isn't on PATH (the fetcher Dockerfile installs it,
    but local + CI may not have it yet)."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media") / "tiny.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:size=64x64:duration=1:rate=10",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=mono:sample_rate=16000:duration=1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


@pytest.fixture(scope="session")
def medium_mp3(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """25-second silent MP3 — long enough to exercise multi-chunk segmentation
    with `segment_time=10`."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media") / "medium.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=mono:sample_rate=16000:duration=25",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "32k",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


@pytest.fixture(scope="session")
def tiny_mp3(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """1-second silent MP3 for whisper-passthrough tests."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media") / "tiny.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=mono:sample_rate=16000:duration=1",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "32k",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out
