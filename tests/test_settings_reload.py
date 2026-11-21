def test_a_changed_setting_applies_without_a_restart(pkc, settings):
    from resources.lib import kodimonitor, sync
    settings['fullSyncInterval'] = '60'
    settings['syncThreadNumber'] = '10'
    pkc.SYNC.load()
    settings['fullSyncInterval'] = '15'
    settings['syncThreadNumber'] = '3'
    kodimonitor.KodiMonitor.onSettingsChanged(None)
    # At once where a running sync does not mind
    assert pkc.SYNC.full_sync_intervall == 15 * 60
    # The rest before the next sync
    assert pkc.SYNC.sync_thread_number == 10
    sync.load_changed_settings()
    assert pkc.SYNC.sync_thread_number == 3
    assert not pkc.SYNC.settings_changed


def test_the_page_size_is_read_when_used(pkc, settings):
    from resources.lib import plex_functions as PF
    settings['limitindex'] = '50'
    assert PF.container_size() == 50
    settings['limitindex'] = ''
    assert PF.container_size() == 200
