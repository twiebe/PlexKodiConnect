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
