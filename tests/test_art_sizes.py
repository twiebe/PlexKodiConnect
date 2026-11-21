import sqlite3

import pytest

from resources.lib import art_sizes

SERVER = 'http://pms:32400'
PATH = 'url=/library/metadata/7/thumb/1700000000&X-Plex-Token=abc'
LEGACY = (SERVER + '/photo/:/transcode?width=1920&height=1920&minSize=1&'
          'upscale=0&' + PATH)


def sized(width):
    return (SERVER + '/photo/:/transcode?width=%d&height=%d&upscale=0&%s'
            % (width, width * 3, PATH))


@pytest.fixture
def settings(monkeypatch):
    values = {key: '0' for key in art_sizes.ART_SETTINGS.values()}
    screen = [1920, 1080]
    reads = []

    def setting(key):
        reads.append(key)
        return values[key]

    monkeypatch.setattr(art_sizes, '_setting', setting)
    monkeypatch.setattr(art_sizes, 'gui_size', lambda: tuple(screen))
    art_sizes.reset()
    yield values, screen, reads
    art_sizes.reset()


def test_auto_fits_the_place_of_each_kind_at_1080(settings):
    assert art_sizes.size_query('poster') == 'width=400&height=1200&upscale=0'
    assert art_sizes.size_query('thumb') == 'width=640&height=1920&upscale=0'
    assert art_sizes.size_query('fanart') == 'width=1920&height=5760&upscale=0'
    assert art_sizes.size_query('logo') == 'width=800&height=2400&upscale=0'


def test_auto_follows_the_gui_and_keeps_backdrops_to_the_screen(settings):
    _, screen, _ = settings
    screen[:] = [3024, 1964]  # 14" MacBook Pro, 16:10
    assert art_sizes.size_query('poster').startswith('width=727&')
    assert art_sizes.size_query('thumb').startswith('width=1164&')
    assert art_sizes.size_query('fanart').startswith('width=3024&')
    screen[:] = [1280, 720]
    art_sizes.reset()
    assert art_sizes.size_query('thumb').startswith('width=427&')


def test_unknown_gui_size_counts_as_1080(settings):
    _, screen, _ = settings
    screen[:] = [0, 0]
    assert art_sizes.size_query('fanart').startswith('width=1920&')


def test_a_width_or_unlimited_per_kind(settings):
    values, _, _ = settings
    values.update({'artSizeThumb': '960', 'artSizeFanart': '-1'})
    assert art_sizes.size_query('thumb') == 'width=960&height=2880&upscale=0'
    # unlimited: what PKC always asked for
    assert art_sizes.size_query('fanart') == \
        'width=1920&height=1920&minSize=1&upscale=0'


def test_unsized_artwork_keeps_the_former_size(settings):
    assert art_sizes.size_query(None) == \
        'width=1920&height=1920&minSize=1&upscale=0'


def test_settings_are_read_once(settings):
    _, _, reads = settings
    for _ in range(50):
        for kind in art_sizes.ART_KINDS + (None,):
            art_sizes.size_query(kind)
        art_sizes.art_key()
    assert len(reads) == 4


def test_the_key_changes_with_a_setting_or_the_gui(settings):
    values, screen, _ = settings
    key = art_sizes.art_key()
    art_sizes.reset()
    assert art_sizes.art_key() == key
    values['artSizeLogo'] = '320'
    art_sizes.reset()
    assert art_sizes.art_key() != key
    values['artSizeLogo'] = '0'
    screen[:] = [3840, 2160]
    art_sizes.reset()
    assert art_sizes.art_key() != key


@pytest.mark.parametrize('plex_art, plex_type, kind', [
    ('thumb', 'movie', 'poster'),
    ('thumb', 'show', 'poster'),
    ('thumb', 'season', 'poster'),
    ('thumb', 'episode', 'thumb'),
    ('thumb', 'clip', 'thumb'),
    ('parentThumb', 'episode', 'poster'),
    ('grandparentThumb', 'episode', 'poster'),
    ('art', 'movie', 'fanart'),
    ('grandparentArt', 'episode', 'fanart'),
    ('banner', 'show', 'logo'),
    ('composite', 'movie', None),
    ('thumb', 'track', None),
    ('thumb', 'album', None),
    ('art', 'artist', None),
    ('parentThumb', 'track', None),
    ('thumb', 'photo', None),
    ('composite', 'playlist', None),
    ('thumb', 'collection', None),
])
def test_art_kind(plex_art, plex_type, kind):
    assert art_sizes.art_kind(plex_art, plex_type) == kind


@pytest.mark.parametrize('kodi_art, media_type, kind', [
    ('poster', 'movie', 'poster'),
    ('poster', 'tvshow', 'poster'),
    ('poster', 'season', 'poster'),
    ('thumb', 'episode', 'thumb'),
    ('thumb', 'movie', 'poster'),
    ('fanart', 'movie', 'fanart'),
    ('fanart3', 'tvshow', 'fanart'),
    ('banner', 'tvshow', 'logo'),
    ('clearlogo', 'movie', 'logo'),
    ('landscape', 'movie', 'thumb'),
    ('poster', 'set', None),
    ('thumb', 'actor', None),
    ('characterart', 'tvshow', None),
])
def test_kodi_art_kind(kodi_art, media_type, kind):
    assert art_sizes.kodi_art_kind(kodi_art, media_type) == kind


def test_resize_url_keeps_the_image_and_the_token(settings):
    assert art_sizes.resize_url(LEGACY, 'thumb') == sized(640)
    assert art_sizes.resize_url(sized(640), 'poster') == sized(400)
    assert art_sizes.resize_url(sized(400), None) == LEGACY


def test_resize_url_keeps_a_composite_with_its_own_size(settings):
    url = (SERVER + '/photo/:/transcode?width=1920&height=1920&minSize=1&'
           'upscale=0&url=/library/collections/9/composite/1%3Fwidth%3D1920'
           '%26height%3D1080&X-Plex-Token=abc')
    assert art_sizes.resize_url(url, 'poster') == (
        SERVER + '/photo/:/transcode?width=400&height=1200&upscale=0&'
        'url=/library/collections/9/composite/1%3Fwidth%3D1920'
        '%26height%3D1080&X-Plex-Token=abc')


def test_resize_url_leaves_other_urls(settings):
    for url in ('https://assets.fanart.tv/fanart/movies/1/x.jpg',
                'image://video@/media/m.mkv/',
                SERVER + '/library/metadata/7/thumb/1'):
        assert art_sizes.resize_url(url, 'poster') == url


def _db(rows):
    con = sqlite3.connect(':memory:')
    con.execute('CREATE TABLE art (art_id INTEGER PRIMARY KEY, media_id '
                'INTEGER, media_type TEXT, type TEXT, url TEXT)')
    con.executemany('INSERT INTO art (media_id, media_type, type, url) '
                    'VALUES (1, ?, ?, ?)', rows)
    return con


def test_resize_urls_rewrites_the_library_to_the_sizes(settings):
    con = _db([
        ('episode', 'thumb', LEGACY),
        ('movie', 'poster', LEGACY),
        ('movie', 'fanart', LEGACY),
        ('tvshow', 'banner', sized(960)),
        ('set', 'poster', LEGACY),
        ('movie', 'clearlogo', 'https://assets.fanart.tv/fanart/movies/1/l.png'),
        ('actor', 'thumb', LEGACY),
    ])
    assert art_sizes.resize_urls(con.cursor()) == 4
    urls = [row[0] for row in con.execute('SELECT url FROM art ORDER BY art_id')]
    assert urls == [
        sized(640), sized(400), sized(1920), sized(800),
        LEGACY, 'https://assets.fanart.tv/fanart/movies/1/l.png', LEGACY,
    ]
    assert art_sizes.resize_urls(con.cursor()) == 0


def test_resize_urls_back_to_unlimited(settings):
    values, _, _ = settings
    con = _db([('episode', 'thumb', sized(640))])
    values['artSizeThumb'] = '-1'
    art_sizes.reset()
    assert art_sizes.resize_urls(con.cursor()) == 1
    assert con.execute('SELECT url FROM art').fetchone()[0] == LEGACY
