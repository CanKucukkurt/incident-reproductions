class CompactionReaderError(Exception):
    """Base class for harness errors."""


class ConfigError(CompactionReaderError):
    pass


class SandboxUnavailable(CompactionReaderError):
    """Raised when the Docker sandbox cannot be used. Never fall back to the host."""


class PathEscapeError(CompactionReaderError):
    """A tool tried to touch a path outside the rollout sandbox."""


class JudgeParseError(CompactionReaderError):
    """A judge did not return valid JSON after all retries."""
