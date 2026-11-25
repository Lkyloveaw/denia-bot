"""Tests for the Linux deployment scripts and SnowLuma compose template."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Linux shell deployment only')

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / 'deploy-linux'
BASH = shutil.which('bash')


def _run(args: list[str], *, cwd: Path = REPO_ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    if not BASH:
        pytest.skip('bash is not available')
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_shell_scripts_parse() -> None:
    scripts = sorted(DEPLOY_DIR.glob('*.sh'))
    assert scripts
    for script in scripts:
        result = _run([BASH, '-n', str(script)])
        assert result.returncode == 0, f'{script}: {result.stderr}'


def test_install_includes_parser_requirements() -> None:
    text = (DEPLOY_DIR / 'install.sh').read_text(encoding='utf-8')
    assert 'nonebot/media_parser/requirements.txt' in text
    assert 'requirement_files+=("$ROOT/nonebot/media_parser/requirements.txt")' in text
    assert 'sha256sum "${requirement_files[@]}"' in text
    assert 'pip_args+=(-r "$requirement_file")' in text


def test_install_check_without_docker_or_systemd() -> None:
    env = os.environ.copy()
    env['PYTHON'] = sys.executable
    result = _run(
        [
            BASH,
            str(DEPLOY_DIR / 'install.sh'),
            '--check',
            '--no-docker',
            '--no-systemd',
            '--root',
            str(REPO_ROOT),
        ],
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert 'prerequisite check passed' in result.stdout


def test_dry_run_does_not_create_runtime_files(tmp_path: Path) -> None:
    root = tmp_path / 'repo'
    (root / 'nonebot' / 'configs').mkdir(parents=True)
    (root / 'docker' / 'snowluma').mkdir(parents=True)
    for relative in ('nonebot/bot.py', 'requirements.lock', 'nonebot/media_parser/requirements.txt'):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, root / relative)
    for relative in ('nonebot/.env.example', 'nonebot/configs/models.yml.example', 'nonebot/configs/templates.yml.example'):
        shutil.copy2(REPO_ROOT / relative, root / relative)
    for relative in ('docker/snowluma/docker-compose.yml', 'docker/snowluma/.env.example'):
        shutil.copy2(REPO_ROOT / relative, root / relative)

    env = os.environ.copy()
    env['PYTHON'] = sys.executable
    result = _run(
        [
            BASH,
            str(DEPLOY_DIR / 'install.sh'),
            '--dry-run',
            '--no-docker',
            '--no-systemd',
            '--no-init',
            '--root',
            str(root),
        ],
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert 'install complete' in result.stdout
    assert 'media_parser/requirements.txt' in result.stdout
    assert not (root / '.venv').exists()
    assert not (root / 'nonebot' / '.env').exists()
    assert not (root / 'compose.yml').exists()


def test_snowluma_compose_template_is_valid() -> None:
    docker = shutil.which('docker')
    if not docker:
        pytest.skip('docker is not available')
    if _run([docker, 'info']).returncode != 0:
        pytest.skip('docker daemon is not available')
    env = os.environ.copy()
    env.update(
        {
            'VNC_PASSWD': 'test-only',
            'BIND_ADDR': '127.0.0.1',
            'SNOWLUMA_DATA_DIR': './snowluma',
        }
    )
    result = _run(
        [
            docker,
            'compose',
            '-f',
            str(REPO_ROOT / 'docker' / 'snowluma' / 'docker-compose.yml'),
            '--project-directory',
            str(REPO_ROOT),
            'config',
            '--quiet',
        ],
        env=env,
    )
    assert result.returncode == 0, result.stderr
