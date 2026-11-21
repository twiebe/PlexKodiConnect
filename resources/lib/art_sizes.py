#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
The width of the artwork PKC asks the PMS for, per kind of image: the PMS
scales it down before Kodi gets it. Kodi itself keeps an image up to imageres
(720 lines) or fanartres (1080 lines) and draws a much larger one on a small
card pixelated, without mipmaps on many devices.

Each kind has a setting: Auto (0) fits the place a skin gives it at the
resolution of the GUI, unlimited (-1) asks the PMS as PKC always did, else the
width in px. Music, photos, playlists and collections keep their size.
"""
from logging import getLogger
import re
from urllib.parse import urlencode

LOG = getLogger('PLEX.art_sizes')

ART_KINDS = ('poster', 'thumb', 'fanart', 'logo')
ART_SETTINGS = {
    'poster': 'artSizePoster',
    'thumb': 'artSizeThumb',
    'fanart': 'artSizeFanart',
    'logo': 'artSizeLogo',
}
# Auto at a GUI of 1080 lines: poster cards (up to 360 px wide in a library
# view, some room for larger ones), episode and landscape cards (two times
# their width), the full screen, a logo box. It follows the GUI height.
ART_AUTO_1080 = {'poster': 400, 'thumb': 640, 'fanart': 1920, 'logo': 800}
# What PKC always asked for, still used for unlimited and unsized artwork
LEGACY_SIZE = (('width', '1920'), ('height', '1920'), ('minSize', '1'),
               ('upscale', '0'))
SIZE_PARAMS = ('width', 'height', 'minSize', 'upscale')

# Plex item types whose artwork is sized
_STILL_TYPES = ('episode', 'clip')
_POSTER_TYPES = ('movie', 'show', 'season')
_VIDEO_TYPES = _STILL_TYPES + _POSTER_TYPES

# Kodi video library: media types PKC writes Plex artwork for
_KODI_STILL_TYPES = ('episode', 'musicvideo')
_KODI_SIZED_TYPES = ('movie', 'tvshow', 'season', 'episode', 'musicvideo')

TRANSCODE = re.compile(r'^(?P<base>.*/photo/:/transcode)\?(?P<query>.*)$')

_queries = None


def _setting(key):
    from .utils import settings
    return settings(key)


def gui_size():
    import xbmcgui
    return xbmcgui.getScreenWidth(), xbmcgui.getScreenHeight()


def art_kind(plex_art, plex_type):
    """
    The size setting of the Plex artwork PLEX_ART ('thumb', 'art', ...) of an
    item of PLEX_TYPE: poster, thumb, fanart, logo, or None to keep its size.
    """
    if plex_type not in _VIDEO_TYPES:
        return None
    if plex_art == 'thumb':
        return 'thumb' if plex_type in _STILL_TYPES else 'poster'
    if plex_art in ('parentThumb', 'grandparentThumb'):
        return 'poster'
    if plex_art in ('art', 'parentArt', 'grandparentArt'):
        return 'fanart'
    if plex_art == 'banner':
        return 'logo'
    return None


def kodi_art_kind(kodi_art, media_type):
    """
    The size setting of the Kodi artwork KODI_ART ('poster', 'fanart1', ...)
    of an item of MEDIA_TYPE in the Kodi video library.
    """
    if media_type not in _KODI_SIZED_TYPES:
        return None
    if kodi_art == 'thumb':
        return 'thumb' if media_type in _KODI_STILL_TYPES else 'poster'
    if kodi_art == 'poster':
        return 'poster'
    if kodi_art == 'landscape':
        return 'thumb'
    if kodi_art.startswith('fanart'):
        return 'fanart'
    if kodi_art in ('banner', 'clearlogo', 'clearart', 'discart'):
        return 'logo'
    return None


def _width(kind, screen_width, height):
    try:
        value = int(_setting(ART_SETTINGS[kind]) or 0)
    except ValueError:
        value = 0
    if value < 0:
        return None
    if value == 0:
        auto = int(round(ART_AUTO_1080[kind] * height / 1080.0))
        # a backdrop fills the screen: no wider than it on a 16:10 or 4:3 screen
        return min(auto, screen_width) if kind == 'fanart' else auto
    return value


def _size_queries():
    global _queries
    if _queries is None:
        screen_width, height = gui_size()
        height = height or 1080
        screen_width = screen_width or height * 16 // 9
        queries = {None: urlencode(LEGACY_SIZE)}
        for kind in ART_KINDS:
            width = _width(kind, screen_width, height)
            # Without minSize the PMS fits the image into the box: the width
            # decides for anything up to three times as high as wide
            queries[kind] = urlencode(
                LEGACY_SIZE if width is None else
                (('width', width), ('height', width * 3), ('upscale', 0)))
        _queries = queries
    return _queries


def size_query(kind):
    """The size parameters of a /photo/:/transcode URL for artwork of KIND
    (see art_kind)."""
    return _size_queries()[kind]


def art_key():
    """What the artwork URLs are written with: changes with any size setting
    and with the GUI resolution Auto follows."""
    queries = _size_queries()
    return '|'.join(queries[kind] for kind in (None,) + ART_KINDS)


def reset():
    """Read the settings and the GUI size anew, e.g. after a settings change."""
    global _queries
    _queries = None


def resize_url(url, kind):
    """URL, a PMS /photo/:/transcode URL, with the size of KIND; any other URL
    as it is."""
    match = TRANSCODE.match(url)
    if not match:
        return url
    # the other parameters (url, X-Plex-Token) stay as they are, quoted
    rest = [part for part in match.group('query').split('&')
            if part and part.split('=', 1)[0] not in SIZE_PARAMS]
    return '%s?%s' % (match.group('base'),
                      '&'.join([size_query(kind)] + rest))


def resize_urls(cursor):
    """
    Write the Plex artwork URLs of the Kodi video library with the current
    sizes. Returns how many changed.
    """
    rows = cursor.execute("SELECT art_id, media_type, type, url FROM art "
                          "WHERE url LIKE '%/photo/:/transcode?%'").fetchall()
    changes = []
    for art_id, media_type, kodi_art, url in rows:
        kind = kodi_art_kind(kodi_art, media_type)
        if kind is None:
            continue
        new = resize_url(url, kind)
        if new != url:
            changes.append((new, art_id))
    cursor.executemany('UPDATE art SET url = ? WHERE art_id = ?', changes)
    LOG.info('artwork sizes: %s URLs rewritten', len(changes))
    return len(changes)
