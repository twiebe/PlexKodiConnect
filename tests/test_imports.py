import importlib

import pytest

MODULES = ['db', 'sync', 'library_sync.full_sync', 'library_sync.process_metadata',
           'library_sync.fill_metadata_queue', 'library_sync.get_metadata',
           'library_sync.websocket', 'plex_functions', 'itemtypes', 'kodi_db', 'plex_db']


@pytest.mark.parametrize('name', MODULES)
def test_sync_modules_import_outside_kodi(name):
    importlib.import_module('resources.lib.' + name)
