import pytest
from lesson3_demo.utils import add


def test_add_positive_numbers():
    assert add(2, 3) == 5


def test_add_negative_numbers():
    assert add(-1, -4) == -5


def test_add_mixed_sign_numbers():
    assert add(-3, 7) == 4


def test_add_zeros():
    assert add(0, 0) == 0


def test_add_floats():
    assert add(1.5, 2.5) == pytest.approx(4.0)
