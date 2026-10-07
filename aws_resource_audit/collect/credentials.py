"""Turns a failed authentication into actionable advice: what was checked, what was
found, how to fix it (naming Docker secrets in a container). No AWS calls; never raises."""

import logging
import configparser
import os

logger = logging.getLogger(__name__)

ENV_KEY = "AWS_ACCESS_KEY_ID"
ENV_SECRET = "AWS_SECRET_ACCESS_KEY"
ENV_TOKEN = "AWS_SESSION_TOKEN"
ENV_PROFILE = "AWS_PROFILE"

CREDENTIALS_FILE_ENV = "AWS_SHARED_CREDENTIALS_FILE"
CONFIG_FILE_ENV = "AWS_CONFIG_FILE"

# Where Docker mounts secrets; the web app delivers credentials as one.
SECRET_DIR = "/run/secrets"

DEFAULT_CREDENTIALS_FILE = "~/.aws/credentials"
DEFAULT_CONFIG_FILE = "~/.aws/config"

# What went wrong, for wording. UNKNOWN (access denied, throttle, network) adds no
# credentials advice, since that would send the reader to fix the wrong thing.
MISSING, NO_PROFILE, EXPIRED, UNKNOWN = "missing", "no-profile", "expired", "unknown"

_MISSING_MARKERS = ("unable to locate credentials", "no credentials")
_NO_PROFILE_MARKERS = ("could not be found", "profilenotfound")
_EXPIRED_MARKERS = ("expired", "invalidclienttokenid", "signaturedoesnotmatch",
                    "security token included in the request is invalid")

DOC = "See iam/README.md for the full setup, including the dedicated read-only role."


def classify(message):
    """Which failure this is, read off botocore's wording (only the text survives safe_call)."""
    text = (message or "").lower()
    if any(m in text for m in _MISSING_MARKERS):
        return MISSING
    if "profile" in text and any(m in text for m in _NO_PROFILE_MARKERS):
        return NO_PROFILE
    if any(m in text for m in _EXPIRED_MARKERS):
        return EXPIRED
    return UNKNOWN


def credentials_file():
    return os.path.expanduser(os.environ.get(CREDENTIALS_FILE_ENV) or DEFAULT_CREDENTIALS_FILE)


def config_file():
    return os.path.expanduser(os.environ.get(CONFIG_FILE_ENV) or DEFAULT_CONFIG_FILE)


def env_credentials_set():
    return bool(os.environ.get(ENV_KEY) and os.environ.get(ENV_SECRET))


def in_container():
    """Whether this process runs in a container. Only ever adds a hint."""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8") as fh:
            return any(m in fh.read() for m in ("docker", "kubepods", "containerd"))
    except OSError:
        return False


def profiles_in(path, is_config=False):
    """Profile names in one credentials or config file, as --profile takes them.
    Unreadable files list nothing."""
    parser = configparser.RawConfigParser()
    try:
        parser.read(path)
    except (OSError, configparser.Error) as e:
        # "No profiles found" is a confusing thing to debug when the real
        # answer is that the file would not parse.
        logger.warning("could not read %s (%s); it will list no profiles",
                       path, e)
        return []
    names = []
    for section in parser.sections():
        if is_config and section.startswith("profile "):
            names.append(section[len("profile "):].strip())
        elif is_config and section != "default":
            continue        # sso-session/services blocks are not profiles
        else:
            names.append(section)
    return sorted(set(names))


def known_profiles():
    return sorted(set(profiles_in(credentials_file())
                      + profiles_in(config_file(), is_config=True)))


def _file_line(label, path, is_config=False):
    if not os.path.exists(path):
        return f"  - {label} {path}: does not exist"
    found = profiles_in(path, is_config=is_config)
    return (f"  - {label} {path}: {'profiles ' + ', '.join(found) if found else 'no profiles defined'}")


def _checked(profile):
    """The four places boto3 looks, in the order it looks, and what is in them."""
    env_profile = os.environ.get(ENV_PROFILE)
    if profile:
        asked = f'profile "{profile}" (requested for this scan)'
    elif env_profile:
        asked = f'profile "{env_profile}" (from {ENV_PROFILE})'
    else:
        asked = 'no profile requested, so the "default" profile is used if one exists'
    return [
        "Checked, in the order boto3 checks them:",
        f"  - {ENV_KEY} / {ENV_SECRET} in the environment: "
        f"{'set' if env_credentials_set() else 'not set'}",
        f"  - {asked}",
        _file_line("credentials file", credentials_file()),
        _file_line("config file", config_file(), is_config=True),
    ]


def is_secret(path):
    """Whether this credentials path is a Docker secret, judged by its path."""
    return path.startswith(SECRET_DIR + os.sep)


def _container_note():
    """Explains a secret path is a read-only mount from the host, not a file to create here."""
    path = credentials_file()
    if is_secret(path):
        return [
            f"{path} is a Docker secret: a read-only file that comes",
            "from the host, not one this container owns. docker-compose.yml takes it from",
            "AUDIT_AWS_CREDENTIALS_FILE, which defaults to ~/.aws-audit/credentials.",
        ]
    if not in_container():
        return []
    return [
        f"This process is running in a container, so {path} is almost",
        "certainly mounted from the host read-only.",
    ]


def _how_to_set(profile):
    """The fix, which is a different instruction on each side of a mount."""
    name = profile or "default"
    if is_secret(credentials_file()) or in_container():
        return [
            "Fix it on the HOST, not in here:",
            f"  1. write a [{name}] section with aws_access_key_id / aws_secret_access_key",
            "     into that host file",
            "  2. `docker-compose up -d --force-recreate` - the file is read when the",
            "     container starts, and a plain `up` will report it as already running.",
        ]
    return [
        "Set credentials in either place:",
        f"  - a profile: `aws configure --profile {name}`, or write a [{name}] section with",
        f"    aws_access_key_id / aws_secret_access_key into {credentials_file()}",
        f"  - the environment: export {ENV_KEY} and {ENV_SECRET} (plus",
        f"    {ENV_TOKEN} for SSO or an already-assumed role) for this process.",
    ]


def _where_to_edit():
    """The file to change, named the way the reader can actually reach it."""
    if is_secret(credentials_file()) or in_container():
        return "the host file behind it, then `docker-compose up --force-recreate`"
    return credentials_file()


def _blocks(*groups):
    """Groups of lines joined into one message, blank-line separated, empties dropped."""
    return "\n\n".join("\n".join(g) for g in groups if g)


def _missing_help(profile):
    return _blocks(["No AWS credentials were found."], _checked(profile),
                   _container_note(), _how_to_set(profile), [DOC])


def _no_profile_help(profile):
    known = known_profiles()
    first = (f'The profile "{profile}" is not defined.' if profile
             else "The requested profile is not defined.")
    have = (f"Profiles that are defined: {', '.join(known)}." if known
            else "No profiles are defined in either file.")
    return _blocks([first, have], _checked(profile), _container_note(),
                   _how_to_set(profile), [DOC])


def _expired_help(profile):
    source = (f'profile "{profile}"' if profile
              else "the environment" if env_credentials_set() else "the default profile")
    return _blocks(
        [f"Credentials were found ({source}) but AWS rejected them as expired or invalid.",
         "Refresh them and try again:",
         "  - SSO: `aws sso login`, then re-export or re-copy the credentials",
         f"  - temporary credentials: a new {ENV_TOKEN} is needed as well as the key and secret",
         f"  - long-lived keys: check the key is still active, then update {_where_to_edit()}"],
        _container_note(), [DOC])


def credentials_help(profile=None, message=""):
    """Guidance for a failed authentication, or "". Printed under botocore's own
    message, never instead of it."""
    kind = classify(message)
    if kind == MISSING:
        return _missing_help(profile)
    if kind == NO_PROFILE:
        return _no_profile_help(profile)
    if kind == EXPIRED:
        return _expired_help(profile)
    return ""
