#!/usr/bin/python3
"""Store the URnetwork provider password in the system secret store.

The GUI and the launcher use this helper instead of keeping the password in
`~/.config/urnetwork/auth`.  It talks to the Secret Service (GNOME Keyring /
KWallet via the libsecret D-Bus API) and therefore needs a running graphical
session.

Usage:
    urnetwork-credentials.py available          # exit 0 if the store works
    urnetwork-credentials.py get EMAIL          # print the password, or nothing
    urnetwork-credentials.py set EMAIL          # read the password from stdin
    urnetwork-credentials.py delete EMAIL       # remove the stored password

The password never appears on the command line: `set` reads it from stdin.
"""

import sys

SCHEMA_NAME = "org.urnetwork.Client"
SERVICE = "urnetwork"


def _secret():
    import gi

    gi.require_version("Secret", "1")
    from gi.repository import Secret

    return Secret


def _schema(Secret):
    return Secret.Schema.new(
        SCHEMA_NAME,
        Secret.SchemaFlags.NONE,
        {
            "service": Secret.SchemaAttributeType.STRING,
            "username": Secret.SchemaAttributeType.STRING,
        },
    )


def _attributes(email):
    return {"service": SERVICE, "username": email}


def main(argv):
    if len(argv) < 2:
        sys.stderr.write(__doc__.strip() + "\n")
        return 2

    command = argv[1]

    try:
        Secret = _secret()
        schema = _schema(Secret)
    except (ImportError, ValueError) as error:
        sys.stderr.write("urnetwork-credentials: secret service unavailable: %s\n" % error)
        return 1

    if command == "available":
        # Round-trip through the store so that a locked or missing keyring is
        # detected here rather than halfway through starting the VPN.
        probe = "__urnetwork_probe__"
        try:
            if Secret.password_lookup_sync(schema, _attributes(probe), None) is None:
                # No probe entry: prove we can write and remove one.
                if not Secret.password_store_sync(
                    schema, _attributes(probe), Secret.COLLECTION_DEFAULT,
                    "URnetwork availability probe", "probe", None,
                ):
                    return 1
                Secret.password_clear_sync(schema, _attributes(probe), None)
        except Exception as error:  # noqa: BLE001 - D-Bus errors are not typed
            sys.stderr.write("urnetwork-credentials: %s\n" % error)
            return 1
        return 0

    if len(argv) < 3 or not argv[2]:
        sys.stderr.write("urnetwork-credentials: an account (email) is required\n")
        return 2

    email = argv[2]
    attributes = _attributes(email)

    try:
        if command == "get":
            password = Secret.password_lookup_sync(schema, attributes, None)
            if password is None:
                return 1
            sys.stdout.write(password)
            return 0

        if command == "set":
            password = sys.stdin.readline().rstrip("\n")
            if not password:
                sys.stderr.write("urnetwork-credentials: empty password on stdin\n")
                return 2
            if not Secret.password_store_sync(
                schema, attributes, Secret.COLLECTION_DEFAULT,
                "URnetwork: %s" % email, password, None,
            ):
                sys.stderr.write("urnetwork-credentials: the secret service refused the entry\n")
                return 1
            return 0

        if command == "delete":
            Secret.password_clear_sync(schema, attributes, None)
            return 0
    except Exception as error:  # noqa: BLE001 - D-Bus errors are not typed
        sys.stderr.write("urnetwork-credentials: %s\n" % error)
        return 1

    sys.stderr.write("urnetwork-credentials: unknown command %r\n" % command)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
