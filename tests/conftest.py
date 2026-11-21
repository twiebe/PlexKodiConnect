"""
Kodi stand-ins for tests outside Kodi: Kodistubs gives the modules, this file
gives the few calls PKC makes at import time answers that work, settings from
resources/settings.xml and a temporary Kodi profile.
"""
import os
import shutil
import sys
import tempfile
import time
import types
import xml.etree.ElementTree as ET

import pytest
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
KODI_HOME = tempfile.mkdtemp(prefix='pkc-test-')
for sub in ('userdata/Database', 'userdata/addon_data/plugin.video.plexkodiconnect',
            'userdata/Thumbnails', 'userdata/playlists/video', 'userdata/library/video',
            'temp'):
    os.makedirs(os.path.join(KODI_HOME, sub), exist_ok=True)


def _defaults():
    values = {}
    for setting in ET.parse(os.path.join(ROOT, 'resources', 'settings.xml')).iter('setting'):
        default = setting.find('default')
        values[setting.get('id')] = (default.text or '') if default is not None else ''
    return values


SETTINGS = _defaults()
PROPERTIES = {}


def translate_path(path):
    for prefix, sub in (('special://profile/', 'userdata/'),
                        ('special://masterprofile/', 'userdata/'),
                        ('special://database/', 'userdata/Database/'),
                        ('special://thumbnails/', 'userdata/Thumbnails/'),
                        ('special://temp/', 'temp/'),
                        ('special://home/', ''),
                        ('special://xbmc/', '')):
        if path.startswith(prefix):
            return os.path.join(KODI_HOME, sub, path[len(prefix):])
    if path in ('special://home', 'special://profile'):
        return os.path.join(KODI_HOME, '' if path.endswith('home') else 'userdata') + os.sep
    return path


class Addon(object):
    def __init__(self, addon_id='plugin.video.plexkodiconnect', id=None):
        self.addon_id = id or addon_id

    def getSetting(self, key):
        return SETTINGS.get(key, '')

    def setSetting(self, key, value):
        SETTINGS[key] = value

    def getAddonInfo(self, key):
        return {
            'profile': 'special://profile/addon_data/%s/' % self.addon_id,
            'path': ROOT,
            'version': '3.12.6',
            'id': self.addon_id,
            'name': 'PlexKodiConnect',
        }.get(key, '')

    def getLocalizedString(self, string_id):
        return 'string %s' % string_id


class Monitor(object):
    def waitForAbort(self, timeout=None):
        # Long enough for the waits that let Kodi write, short for tests
        time.sleep(min(timeout or 0, 0.2))
        return False

    def abortRequested(self):
        return False


class Window(object):
    def __init__(self, window_id=-1):
        pass

    def getProperty(self, key):
        return PROPERTIES.get(key, '')

    def setProperty(self, key, value):
        PROPERTIES[key] = value

    def clearProperty(self, key):
        PROPERTIES.pop(key, None)


xbmc.getInfoLabel = lambda label: {
    'System.BuildVersion': '21.2 (21.2.0) Git:20250101-test',
    'System.FriendlyName': 'pkc-test'}.get(label, '')
xbmc.getCondVisibility = lambda condition: False
xbmc.executebuiltin = lambda *args, **kwargs: None
xbmc.executeJSONRPC = lambda request: '{"result": {}}'
xbmc.log = lambda *args, **kwargs: None
xbmc.Monitor = Monitor
xbmcvfs.translatePath = translate_path
xbmcvfs.exists = lambda path: os.path.exists(translate_path(path))
xbmcvfs.mkdirs = lambda path: os.makedirs(translate_path(path), exist_ok=True) or True
xbmcaddon.Addon = Addon
xbmcgui.Window = Window
xbmcgui.getScreenWidth = lambda: 1920
xbmcgui.getScreenHeight = lambda: 1080


# metadata.themoviedb.org.python, imported by PKC from Kodi's add-on folder
_tmdb = types.ModuleType('tmdbscraper.tmdb')


class TMDBMovieScraper(object):
    def __init__(self, url_settings, language, certification_country,
                 search_language=''):
        pass


_tmdb.TMDBMovieScraper = TMDBMovieScraper
sys.modules['tmdbscraper'] = types.ModuleType('tmdbscraper')
sys.modules['tmdbscraper'].tmdb = _tmdb
sys.modules['tmdbscraper.tmdb'] = _tmdb


@pytest.fixture
def settings():
    """Change settings for one test."""
    saved = dict(SETTINGS)
    yield SETTINGS
    SETTINGS.clear()
    SETTINGS.update(saved)


def _sql(name):
    with open(os.path.join(ROOT, 'tests', 'data', name)) as f:
        return f.read()


@pytest.fixture
def dbs(monkeypatch):
    """Empty Kodi 21 video and texture DBs and a set-up plex.db, as PKC opens
    them, fresh for each test."""
    import sqlite3
    from resources.lib import variables as v, utils
    folder = tempfile.mkdtemp(dir=KODI_HOME)
    paths = {}
    for attr, name, sql in (('DB_VIDEO_PATH', 'MyVideos131.db', 'myvideos131.sql'),
                            ('DB_TEXTURE_PATH', 'Textures13.db', 'textures13.sql'),
                            ('DB_MUSIC_PATH', 'MyMusic83.db', 'mymusic83.sql')):
        paths[attr] = os.path.join(folder, name)
        con = sqlite3.connect(paths[attr])
        con.executescript(_sql(sql))
        con.close()
        monkeypatch.setattr(v, attr, paths[attr])
    for attr, name in (('DB_PLEX_PATH', 'plex.db'),
                       ('DB_PLEX_COPY_PATH', 'plex-copy.db')):
        paths[attr] = os.path.join(folder, name)
        monkeypatch.setattr(v, attr, paths[attr])
    monkeypatch.setattr(v, 'DB_VIDEO_VERSION', 131)
    monkeypatch.setattr(v, 'DB_TEXTURE_VERSION', 13)
    monkeypatch.setattr(v, 'DB_MUSIC_VERSION', 83)
    utils.init_dbs()
    yield paths
    shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
def pkc(dbs):
    """PKC's state as the service sets it up, syncing the section of the
    tests.plex PMS"""
    from resources.lib import app
    app.init()
    app.APP.monitor = xbmc.Monitor()
    app.SYNC.section_ids = {1}
    yield app
    # Let threads a test left hanging go
    app.APP.stop_pkc = True


def pytest_sessionfinish(session, exitstatus):
    # PKC's background workers are no daemons: without this pytest never ends
    from resources.lib import backgroundthread
    backgroundthread.BGThreader.shutdown(block=False)
    shutil.rmtree(KODI_HOME, ignore_errors=True)


def pytest_configure(config):
    # PKC's own warnings (utcnow, LOG.warn, regex escapes) are not the tests'
    for category in ('DeprecationWarning', 'SyntaxWarning'):
        config.addinivalue_line('filterwarnings', 'ignore::%s' % category)
