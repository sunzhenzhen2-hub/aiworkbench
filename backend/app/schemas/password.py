from pydantic import field_validator

from app.core.password_policy import PASSWORD_POLICY_RULES, validate_strong_password


class StrongPasswordMixin:
    @field_validator("password", "new_password", check_fields=False)
    @classmethod
    def check_strong_password(cls, value: str) -> str:
        return validate_strong_password(value)


def password_policy_response() -> dict:
    return {"rules": PASSWORD_POLICY_RULES}
