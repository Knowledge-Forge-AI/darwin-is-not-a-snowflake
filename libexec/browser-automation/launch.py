"""Credential-free stock Brave adapter for automation engines' fresh profiles.

The installed Nix entrypoint is held before reaching this code. Source release
requires native policy and MCP qualification; preference seeding is not proof of
effective behavior under inherited policy. Never accept a reusable personal root.
"""
import json
import os
from pathlib import Path
import sys
import tempfile

PREFERENCES = {
    "credentials_enable_service": False,
    "credentials_enable_autosignin": False,
    "credentials_enable_passkeys": False,
    "autofill": {"profile_enabled": False, "credit_card_enabled": False},
}
FORBIDDEN = (
    "--load-extension", "--disable-extensions-except", "--profile-directory",
    "--remote-debugging-address", "--remote-allow-origins", "--disable-web-security",
    "--no-sandbox", "--password-store", "--enable-sync", "--disable-extensions",
)
# Current Playwright/Puppeteer defaults inspected from their primary source.
# Restore native Shields/components and credential backend; do not add a flag fleet.
OMIT_FRAMEWORK_DEFAULTS = {
    "--disable-extensions", "--disable-component-extensions-with-background-pages",
    "--disable-component-update", "--password-store=basic",
    "--disable-client-side-phishing-detection", "--unsafely-disable-devtools-self-xss-warnings",
    "--no-sandbox",  # Playwright's default: keep Brave's native sandbox enabled.
}
ALLOWED_ENGINE_FEATURES = {"CDPScreenshotNewSurface", "PdfOopif"}


def engine_arguments(arguments):
    result = []
    for argument in arguments:
        if argument in OMIT_FRAMEWORK_DEFAULTS:
            continue
        if argument.startswith("--enable-features"):
            features = argument.partition("=")[2].split(",")
            if not set(features) <= ALLOWED_ENGINE_FEATURES:
                raise ValueError("UNQUALIFIED_ENGINE_FEATURE")
        if argument.startswith("--disable-features="):
            features = [name for name in argument.split("=", 1)[1].split(",") if name != "HttpsUpgrades"]
            if not features:
                continue
            argument = "--disable-features=" + ",".join(features)
        result.append(argument)
    return result


def user_data_directory(arguments):
    """Accept exactly one engine-owned empty temporary data directory."""
    values = [arg.split("=", 1)[1] for arg in arguments if arg.startswith("--user-data-dir=")]
    if len(values) != 1:
        raise ValueError("ONE_FRESH_AUTOMATION_DATA_DIRECTORY_REQUIRED")
    raw = Path(values[0])
    if not raw.is_absolute() or raw.is_symlink() or not raw.is_dir():
        raise ValueError("AUTOMATION_DATA_DIRECTORY_INVALID")
    path = raw.resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if not any(root == temporary_root or root in temporary_root.parents
               for root in (Path("/private/tmp"), Path("/private/var/folders"))):
        raise ValueError("NATIVE_TEMPORARY_ROOT_REQUIRED")
    if path == temporary_root or temporary_root not in path.parents:
        raise ValueError("AUTOMATION_DIRECTORY_OUTSIDE_TEMPORARY_ROOT")
    if path.stat().st_uid != os.getuid() or any(path.iterdir()):
        raise ValueError("AUTOMATION_DIRECTORY_NOT_EMPTY_OR_NOT_OWNED")
    return path


def prepare(binary, arguments):
    """Validate before writing, then seed only the new engine scratch profile."""
    if not Path(binary).is_absolute() or not Path(binary).is_file():
        raise ValueError("STOCK_BRAVE_EXECUTABLE_REQUIRED")
    arguments = engine_arguments(arguments)
    if any(arg == flag or arg.startswith(flag + "=") for flag in FORBIDDEN for arg in arguments):
        raise ValueError("UNSAFE_AUTOMATION_ARGUMENT")
    if any(arg == "--user-data-dir" for arg in arguments):
        raise ValueError("USE_SINGLE_EQUALS_FORM_DATA_DIRECTORY")
    debug = [arg for arg in arguments if arg.startswith("--remote-debugging-port")]
    if debug and debug != ["--remote-debugging-port=0"]:
        raise ValueError("ONLY_EPHEMERAL_DEBUGGING_PORT_ALLOWED")
    profile = user_data_directory(arguments)
    profile.chmod(0o700)
    default = profile / "Default"
    default.mkdir(mode=0o700)
    preferences = default / "Preferences"
    with preferences.open("x", encoding="utf-8") as stream:
        json.dump(PREFERENCES, stream)
    preferences.chmod(0o600)
    extra = ["--disable-sync", "--no-first-run"]
    if debug:
        extra.append("--remote-debugging-address=127.0.0.1")
    return [binary, *arguments, *extra]


def main():
    try:
        command = prepare(sys.argv[1], sys.argv[2:])
    except (ValueError, OSError, IndexError) as error:
        # Never print a path, user identity or provider's raw filesystem error.
        print(str(error) if isinstance(error, ValueError) else "AUTOMATION_PREPARATION_FAILED", file=sys.stderr)
        return 78
    os.execv(command[0], command)


if __name__ == "__main__":
    raise SystemExit(main())
