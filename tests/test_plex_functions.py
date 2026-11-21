import pytest

from resources.lib import plex_functions as PF


class Response(object):
    def __init__(self, status_code, plex_ids=()):
        self.status_code = status_code
        self.content = ('<MediaContainer>%s</MediaContainer>' % ''.join(
            '<Video ratingKey="%s"/>' % x for x in plex_ids)).encode()


@pytest.fixture
def pms(monkeypatch):
    """A PMS with the items 1 to 100 except 7; answers a request for several
    items with 404 if one is missing, as we cannot rule out it does"""
    state = {'have': set(range(1, 101)) - {7}, 'down': False, 'requests': []}

    class DU(object):
        def downloadUrl(self, url, parameters=None, return_response=False):
            assert return_response
            asked = [int(x) for x in url.rsplit('/', 1)[1].split(',')]
            state['requests'].append(asked)
            if state['down']:
                return None
            if not set(asked) <= state['have']:
                return Response(404)
            return Response(200, asked)

    monkeypatch.setattr(PF, 'DU', DU)
    return state


def test_asks_for_items_in_batches(pms):
    assert PF.plex_ids_on_pms(range(1, 7)) == {1, 2, 3, 4, 5, 6}
    assert PF.plex_ids_on_pms(range(8, 108)) == set(range(8, 101))
    assert [len(x) for x in pms['requests']][:3] == [6, 50, 50]


def test_a_batch_with_a_missing_item_is_asked_item_by_item(pms):
    assert PF.plex_ids_on_pms([5, 6, 7, 8]) == {5, 6, 8}
    assert pms['requests'] == [[5, 6, 7, 8], [5], [6], [7], [8]]


def test_none_if_the_pms_does_not_answer(pms):
    pms['down'] = True
    assert PF.plex_ids_on_pms([1, 2]) is None
