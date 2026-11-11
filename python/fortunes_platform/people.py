"""Hand everything a person owns to someone else, or erase it.

Built to retire `local`, the shared identity from before sign-in, and kept
for any person: an admin can hand one person's data to another, and erase an
account (theirs, or a person's own from the account page).

The journal's part is the same in every app and is done here, in one
transaction, so running it again changes nothing more. The files an app keeps
for each person (Tarotarium's drafts, I Ching's texts) are the app's own: it
hands in a module with two steps, and what each returns is added to the
report.

    def move_files(src: str, dst: str) -> dict: ...
    def erase_files(username: str) -> dict: ...

Left as they are on purpose: a share granted to someone stays theirs, the
usage ledger keeps its rows (it is the instance's accounting), and a handed-on
account's own row stays, deactivated, so older references still resolve.
"""

from collections.abc import Callable

from fortunes_platform import db, sessions, users

__all__ = ["reassign", "erase"]


def reassign(src: str, dst: str, move_files: Callable[[str, str], dict]) -> dict:
    """Move `src`'s readings, settings and spending to `dst`, then the app's
    files, and deactivate `src` (signed out everywhere)."""
    report: dict = {"from": src, "to": dst}
    with db.connect() as con:
        report["readings"] = con.execute(
            "UPDATE readings SET owner = ? WHERE owner = ?", (dst, src)).rowcount
        # where both have a setting, the destination's own choice wins
        con.execute(
            "INSERT OR IGNORE INTO user_settings (owner, key, value)"
            " SELECT ?, key, value FROM user_settings WHERE owner = ?", (dst, src))
        report["settings"] = con.execute(
            "DELETE FROM user_settings WHERE owner = ?", (src,)).rowcount
        # Spending follows the person. A charge for the same reading on the
        # same day can exist for both; the destination's stands and the
        # source's is dropped.
        con.execute(
            "UPDATE OR IGNORE reading_charges SET owner = ? WHERE owner = ?", (dst, src))
        con.execute("DELETE FROM reading_charges WHERE owner = ?", (src,))
        report["usage_rows"] = con.execute(
            "UPDATE ai_usage SET owner = ? WHERE owner = ?", (dst, src)).rowcount

    report.update(move_files(src, dst))

    sessions.destroy_all(src)
    users.update(src, active=False)
    report["deactivated"] = True
    return report


def erase(username: str, erase_files: Callable[[str], dict]) -> dict:
    """Erase an account: its readings (their interpretations and shares go
    with them), the shares it was given, its settings, charges and sessions,
    then the app's files, then the account itself. Who may be erased is for
    the routes to decide."""
    report: dict = {"user": username}
    with db.connect() as con:
        report["readings"] = con.execute(
            "DELETE FROM readings WHERE owner = ?", (username,)).rowcount
        report["shares_received"] = con.execute(
            "DELETE FROM reading_shares WHERE grantee = ?", (username,)).rowcount
        report["settings"] = con.execute(
            "DELETE FROM user_settings WHERE owner = ?", (username,)).rowcount
        con.execute("DELETE FROM reading_charges WHERE owner = ?", (username,))
        con.execute("DELETE FROM sessions WHERE username = ?", (username,))

    report.update(erase_files(username))

    with db.connect() as con:
        con.execute("DELETE FROM users WHERE username = ?", (username,))
    report["deleted"] = True
    return report
