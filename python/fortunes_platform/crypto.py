"""Encryption-at-rest for stored credentials (LLM API keys).

Fernet key comes from the app's `<prefix>SECRET_KEY`, or is generated once
and kept in its data directory as .secret_key (mode 0600). Values are stored as 'enc:<token>';
the database alone is not enough to recover a credential.
"""


from cryptography.fernet import Fernet, InvalidToken

from fortunes_platform import host
from fortunes_platform.host import data_dir

PREFIX = "enc:"


def _key() -> bytes:
    env = host.env("SECRET_KEY").strip()
    if env:
        return env.encode()
    key_file = data_dir() / ".secret_key"
    if key_file.is_file():
        return key_file.read_bytes().strip()
    data_dir().mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    key_file.touch(mode=0o600)
    key_file.write_bytes(key)
    return key


def encrypt(value: str) -> str:
    return PREFIX + Fernet(_key()).encrypt(value.encode()).decode()


def decrypt(stored: str) -> str:
    """Returns '' if the value can't be decrypted (rotated/lost key)."""
    if not stored.startswith(PREFIX):
        return stored  # legacy/plaintext passthrough
    try:
        return Fernet(_key()).decrypt(stored[len(PREFIX):].encode()).decode()
    except InvalidToken:
        return ""
