import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import system_info

SNAPSHOT_KEYS = {
    'hostname',
    'os',
    'arch',
    'python_implementation',
    'python_version',
    'compiler',
    'cpu',
    'cpu_cores',
    'cpu_threads',
    'executable',
    'pid',
    'boot_time',
    'uptime_seconds',
    'dependencies',
}


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    system_info.clear_cache()
    monkeypatch.setattr(system_info, 'metadata', SimpleNamespace(version=lambda name: '1.0.0'))
    yield
    system_info.clear_cache()


def stub_platform(monkeypatch):
    monkeypatch.setattr(system_info.platform, 'node', lambda: 'test-host')
    monkeypatch.setattr(system_info.platform, 'system', lambda: 'Linux')
    monkeypatch.setattr(system_info.platform, 'release', lambda: '6.8.0')
    monkeypatch.setattr(system_info.platform, 'version', lambda: '#1 SMP PREEMPT')
    monkeypatch.setattr(system_info.platform, 'machine', lambda: 'x86_64')
    monkeypatch.setattr(system_info.platform, 'processor', lambda: 'AMD Ryzen 7')
    monkeypatch.setattr(system_info.platform, 'python_implementation', lambda: 'CPython')
    monkeypatch.setattr(system_info.platform, 'python_version', lambda: '3.12.10')
    monkeypatch.setattr(system_info.platform, 'python_compiler', lambda: 'GCC 13.2.0')


def stub_psutil(monkeypatch):
    monkeypatch.setattr(system_info.psutil, 'cpu_count', lambda *, logical=True: 8 if logical else 4)
    monkeypatch.setattr(system_info.psutil, 'boot_time', lambda: 1_700_000_000.0)


def test_snapshot_shape_and_types(monkeypatch):
    stub_platform(monkeypatch)
    stub_psutil(monkeypatch)

    data = system_info.snapshot()

    assert set(data) == SNAPSHOT_KEYS
    for key in SNAPSHOT_KEYS - {'pid', 'cpu_cores', 'cpu_threads', 'boot_time', 'uptime_seconds', 'dependencies'}:
        assert isinstance(data[key], str), key
    assert data['hostname'] == 'test-host'
    assert data['os'] == 'Linux 6.8.0'
    assert data['arch'] == 'x86_64'
    assert data['cpu'] == 'AMD Ryzen 7'
    assert data['python_implementation'] == 'CPython'
    assert data['python_version'] == '3.12.10'
    assert data['compiler'] == 'GCC 13.2.0'
    assert data['cpu_cores'] == 4
    assert data['cpu_threads'] == 8
    assert data['executable'] == sys.executable
    assert isinstance(data['pid'], int)
    assert data['boot_time'] == 1_700_000_000.0
    assert isinstance(data['uptime_seconds'], int)
    assert data['uptime_seconds'] >= 0

    assert isinstance(data['dependencies'], list)
    assert len(data['dependencies']) == len(system_info.DEPENDENCIES)
    assert [item['name'] for item in data['dependencies']] == list(system_info.DEPENDENCIES)
    for item in data['dependencies']:
        assert set(item) == {'name', 'version'}
        assert item['version'] == '1.0.0'

    assert json.loads(json.dumps(data)) == data


def test_windows_build_selects_windows_11(monkeypatch):
    monkeypatch.setattr(system_info.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(system_info.platform, 'release', lambda: '10')
    monkeypatch.setattr(system_info.platform, 'version', lambda: '10.0.22631')

    assert system_info.snapshot(cache_seconds=0)['os'] == 'Windows 11'

    system_info.clear_cache()
    monkeypatch.setattr(system_info.platform, 'version', lambda: '10.0.19045')

    assert system_info.snapshot(cache_seconds=0)['os'] == 'Windows 10'


def test_cpu_falls_back_to_uname_then_unknown(monkeypatch):
    stub_platform(monkeypatch)
    monkeypatch.setattr(system_info.platform, 'processor', lambda: '')
    monkeypatch.setattr(system_info.platform, 'uname', lambda: SimpleNamespace(processor='ARMv8'))

    assert system_info.snapshot(cache_seconds=0)['cpu'] == 'ARMv8'

    system_info.clear_cache()
    monkeypatch.setattr(system_info.platform, 'uname', lambda: SimpleNamespace(processor=''))

    assert system_info.snapshot(cache_seconds=0)['cpu'] == 'Unknown'


def test_missing_psutil_degrades_to_none(monkeypatch):
    monkeypatch.setattr(system_info, 'psutil', None)

    data = system_info.snapshot(cache_seconds=0)

    assert data['cpu_cores'] is None
    assert data['cpu_threads'] is None
    assert data['boot_time'] is None
    assert data['uptime_seconds'] is None


def test_dependencies_include_missing_packages(monkeypatch):
    seen = []

    class FakeMetadata:
        @staticmethod
        def version(name):
            seen.append(name)
            if name == 'sqlalchemy':
                return '2.0.36'
            if name == 'pydantic':
                return None
            raise LookupError(name)

    monkeypatch.setattr(system_info, 'metadata', FakeMetadata)

    dependencies = system_info.snapshot(cache_seconds=0)['dependencies']
    versions = {item['name']: item['version'] for item in dependencies}

    assert list(versions) == list(system_info.DEPENDENCIES)
    assert versions['SQLAlchemy'] == '2.0.36'
    assert versions['pydantic'] == ''
    assert all(version == '' for name, version in versions.items() if name not in {'SQLAlchemy', 'pydantic'})
    assert 'sqlalchemy' in seen
    assert len(seen) == len(system_info.DEPENDENCIES)


def test_missing_metadata_degrades_to_empty_versions(monkeypatch):
    monkeypatch.setattr(system_info, 'metadata', None)

    dependencies = system_info.snapshot(cache_seconds=0)['dependencies']

    assert [item['name'] for item in dependencies] == list(system_info.DEPENDENCIES)
    assert all(item['version'] == '' for item in dependencies)


def test_cache_hit_does_not_call_psutil_again(monkeypatch):
    calls = []

    def cpu_count(*, logical=True):
        calls.append(logical)
        return 8 if logical else 4

    monkeypatch.setattr(system_info.psutil, 'cpu_count', cpu_count)
    monkeypatch.setattr(system_info.psutil, 'boot_time', lambda: 1_000.0)

    first = system_info.snapshot(cache_seconds=60)
    second = system_info.snapshot(cache_seconds=60)

    assert first == second
    assert calls == [False, True]


def test_cache_expires_after_cache_seconds(monkeypatch):
    clock = {'now': 100.0}
    calls = []

    def cpu_count(*, logical=True):
        calls.append(logical)
        return 2

    monkeypatch.setattr(system_info.time, 'monotonic', lambda: clock['now'])
    monkeypatch.setattr(system_info.psutil, 'cpu_count', cpu_count)
    monkeypatch.setattr(system_info.psutil, 'boot_time', lambda: 0.0)

    system_info.snapshot(cache_seconds=10)
    clock['now'] = 105.0
    system_info.snapshot(cache_seconds=10)
    assert len(calls) == 2

    clock['now'] = 111.0
    system_info.snapshot(cache_seconds=10)
    assert len(calls) == 4


def test_zero_cache_seconds_bypasses_cache(monkeypatch):
    calls = []

    def cpu_count(*, logical=True):
        calls.append(logical)
        return 2

    monkeypatch.setattr(system_info.psutil, 'cpu_count', cpu_count)
    monkeypatch.setattr(system_info.psutil, 'boot_time', lambda: 0.0)

    system_info.snapshot(cache_seconds=0)
    system_info.snapshot(cache_seconds=0)

    assert len(calls) == 4


def test_clear_cache_collects_fresh_values(monkeypatch):
    threads = {'value': 2}

    monkeypatch.setattr(system_info.psutil, 'cpu_count', lambda *, logical=True: threads['value'])
    monkeypatch.setattr(system_info.psutil, 'boot_time', lambda: 0.0)

    assert system_info.snapshot()['cpu_threads'] == 2
    threads['value'] = 8
    assert system_info.snapshot()['cpu_threads'] == 2

    system_info.clear_cache()

    assert system_info.snapshot()['cpu_threads'] == 8
