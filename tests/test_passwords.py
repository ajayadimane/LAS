import pytest

from app.core.passwords import temporary_password


@pytest.mark.parametrize("length", [8, 12, 32])
def test_has_the_length_and_every_character_class(length):
    for _ in range(50):
        p = temporary_password(length)
        assert len(p) == length
        assert any(c.isupper() for c in p)
        assert any(c.islower() for c in p)
        assert any(c.isdigit() for c in p)
        assert any(c in "@#$%" for c in p)


def test_avoids_look_alike_characters():
    sample = "".join(temporary_password(64) for _ in range(50))
    assert not set(sample) & set("0O1lI")


def test_rejects_a_length_too_short_for_every_class():
    with pytest.raises(ValueError):
        temporary_password(3)
