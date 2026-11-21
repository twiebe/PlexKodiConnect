"""
The fork's version, 1:3.12.6-1, compares as 3.12.6 where PKC checks versions.
"""
import pytest

from resources.lib import utils


@pytest.mark.parametrize('current, minimum, expected', [
    ('3.12.6', '3.9.4', True),
    ('3.9.3', '3.9.4', False),
    ('1:3.12.6-1', '3.9.4', True),
    ('1:3.12.6-1', '3.12.6', True),
    ('1:3.0.3-2', '3.0.4', False),
    ('', '3.0.4', True),
    ('none', '3.0.4', True),
])
def test_compare_version(current, minimum, expected):
    assert utils.compare_version(current, minimum) is expected


@pytest.mark.parametrize('version, expected', [
    ('1:3.12.6-1', '3.12.6'),
    ('3.12.6', '3.12.6'),
    ('', ''),
])
def test_upstream_version(version, expected):
    assert utils.upstream_version(version) == expected


@pytest.mark.parametrize('stored', ['3.12.6', '1:3.12.6-1', ''])
def test_settings_keep_versions_upstream_can_read(settings, monkeypatch, stored):
    """PKC of upstream raises on 1:3.12.6-1 in these, see compare_version"""
    from resources.lib import migration, variables as v
    monkeypatch.setattr(v, 'ADDON_VERSION', '1:3.12.6-2')
    settings['last_migrated_PKC_version'] = stored
    settings['dbCreatedWithVersion'] = stored
    migration.check_migration()
    assert settings['last_migrated_PKC_version'] == '3.12.6'
    assert settings['dbCreatedWithVersion'] == stored.replace('1:', '').replace('-1', '')
