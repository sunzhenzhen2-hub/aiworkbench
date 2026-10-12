import re

WEAK_PASSWORDS = frozenset({
    "password", "123456", "12345678", "admin123", "manager123",
    "owner123", "member123", "qwerty", "abc123", "111111",
})

PASSWORD_POLICY_RULES = [
    "至少 8 位，不超过 128 位",
    "至少 1 个大写字母（A-Z）",
    "至少 1 个小写字母（a-z）",
    "至少 1 个数字（0-9）",
    "须同时包含数字与大小写字母混合",
    "不能是常见弱密码",
]


def validate_strong_password(password: str) -> str:
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    if len(password) > 128:
        raise ValueError("密码不能超过 128 位")
    if password.lower() in WEAK_PASSWORDS:
        raise ValueError("密码过于简单，请更换")
    if not re.search(r"[A-Z]", password):
        raise ValueError("密码须包含大写字母")
    if not re.search(r"[a-z]", password):
        raise ValueError("密码须包含小写字母")
    if not re.search(r"\d", password):
        raise ValueError("密码须包含数字")
    return password


def is_strong_password(password: str) -> bool:
    try:
        validate_strong_password(password)
        return True
    except ValueError:
        return False
