"""Checks for the pip index diagnostics in the Windows startup preflight."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO_ROOT / 'tools' / 'startup-preflight.ps1'
LAUNCHER = REPO_ROOT / 'tools' / '启动所有.ps1'
OPERATIONS = REPO_ROOT / 'docs' / 'OPERATIONS.md'


def test_preflight_keeps_index_diagnostics_ascii() -> None:
    # PowerShell 5.1 reads a BOM-less .ps1 as GBK; keep this file ASCII-only.
    PREFLIGHT.read_text(encoding='ascii')


def test_preflight_reports_and_forwards_index() -> None:
    text = PREFLIGHT.read_text(encoding='utf-8')
    assert '[string]$IndexUrl' in text
    assert '[switch]$IndexProbeOnly' in text
    assert 'PIP_INDEX_URL' in text
    assert 'pip config list' in text
    assert '--index-url' in text
    assert 'pypi.tuna.tsinghua.edu.cn' in text
    assert 'NO_PROXY' in text
    assert 'proxy' in text.lower()


def test_preflight_installs_media_parser_requirements() -> None:
    text = PREFLIGHT.read_text(encoding='utf-8')
    assert "$parserRequirements = Join-Path $botDir 'media_parser\\requirements.txt'" in text
    assert "$pipArgs += @('-r', $parserRequirements)" in text
    assert '$requirementsFiles += $parserRequirements' in text
    assert 'media parser requirements' in text


def test_launcher_forwards_index_url() -> None:
    text = LAUNCHER.read_text(encoding='utf-8-sig')
    assert '[string]$IndexUrl' in text
    assert "$preflightArgs['IndexUrl'] = $IndexUrl" in text


def test_operations_documents_index_configuration() -> None:
    text = OPERATIONS.read_text(encoding='utf-8')
    assert 'PIP_INDEX_URL' in text
    assert 'startup-preflight.ps1 -IndexUrl' in text
    assert 'pypi.tuna.tsinghua.edu.cn' in text


def _powershell() -> str:
    exe = shutil.which('powershell')
    if not exe:
        pytest.skip('powershell.exe is not available')
    return exe


def _probe(
    root: Path,
    *args: str,
    index_url: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for key in (
        'PIP_INDEX_URL',
        'PIP_EXTRA_INDEX_URL',
        'HTTP_PROXY',
        'HTTPS_PROXY',
        'NO_PROXY',
        'http_proxy',
        'https_proxy',
        'no_proxy',
    ):
        env.pop(key, None)
    if index_url is not None:
        env['PIP_INDEX_URL'] = index_url
    if extra_env:
        env.update(extra_env)
    env['QQBOT_NO_PAUSE'] = '1'
    return subprocess.run(
        [
            _powershell(),
            '-NoProfile',
            '-ExecutionPolicy',
            'Bypass',
            '-File',
            str(PREFLIGHT),
            '-Root',
            str(root),
            '-IndexProbeOnly',
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell only')
class TestStartupPreflightIndex:
    def test_default_index_is_visible(self, tmp_path: Path) -> None:
        result = _probe(tmp_path)
        assert result.returncode == 0, result.stderr
        assert 'https://pypi.org/simple' in result.stdout

    def test_environment_index_credentials_are_redacted(self, tmp_path: Path) -> None:
        result = _probe(tmp_path, index_url='https://user:secret@mirror.example/simple')
        assert result.returncode == 0, result.stderr
        assert 'mirror.example' in result.stdout
        assert 'secret' not in result.stdout
        assert 'user:' not in result.stdout

    def test_one_off_index_is_visible(self, tmp_path: Path) -> None:
        result = _probe(tmp_path, '-IndexUrl', 'https://mirror.example/simple')
        assert result.returncode == 0, result.stderr
        assert 'one-off -IndexUrl: https://mirror.example/simple' in result.stdout

    def test_proxy_environment_is_visible_and_redacted(self, tmp_path: Path) -> None:
        result = _probe(
            tmp_path,
            extra_env={
                'HTTPS_PROXY': 'http://user:secret@proxy.example:8080',
                'NO_PROXY': 'localhost',
            },
        )
        assert result.returncode == 0, result.stderr
        assert 'HTTPS_PROXY: http://proxy.example:8080' in result.stdout
        assert 'NO_PROXY: localhost' in result.stdout
        assert 'secret' not in result.stdout
