from quitbot_backend.auth import AccessKeys, PasswordChange


def authenticated(access: AccessKeys) -> str:
    setup = access.login(access.initial_password)
    return access.change_password(
        setup.access_token, PasswordChange(new_password="fixture-password-only")
    ).access_token
