"""tkt error types. All carry a human message; the CLI prints to stderr and
exits non-zero so skills never see a silent failure."""


class TktError(Exception):
    """Base for all tkt errors. exit_code drives the process exit status."""

    exit_code = 1


class ConfigError(TktError):
    """Missing/invalid .sdlc/config.toml or a referenced key."""

    exit_code = 2


class ProviderError(TktError):
    """The backend (Jira/GitHub/...) returned an error or is unreachable."""

    exit_code = 3


class NotFoundError(TktError):
    """A ticket, lane role, or named query does not exist."""

    exit_code = 4


class UsageError(TktError):
    """Bad CLI arguments."""

    exit_code = 64
