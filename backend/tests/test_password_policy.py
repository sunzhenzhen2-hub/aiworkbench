import pytest

from app.core.password_policy import validate_strong_password


@pytest.mark.parametrize(
    "password,ok",
    [
        ("InitManager2026", True),
        ("MyPass2026", True),
        ("short1", False),
        ("alllowercase1", False),
        ("ALLUPPERCASE1", False),
        ("NoNumberPass", False),
        ("manager123", False),
    ],
)
def test_password_strength(password: str, ok: bool) -> None:
    if ok:
        assert validate_strong_password(password) == password
    else:
        with pytest.raises(ValueError):
            validate_strong_password(password)
