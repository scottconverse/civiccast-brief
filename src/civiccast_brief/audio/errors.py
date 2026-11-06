"""Error types for the audio package. The CLI turns each of them into a one-line message."""


class AudioError(RuntimeError):
    """Base class for failures the operator can act on: a bad script, config, tool or box."""


class ScriptError(AudioError, ValueError):
    """The script text does not follow the line format."""


class ConfigError(AudioError):
    """The config file cannot be read, or has unknown or badly typed keys."""


class ToolError(AudioError):
    """ffmpeg or ffprobe is missing, or one of their calls failed."""


class EngineError(AudioError):
    """A voice engine failed, or the GPU box did not finish the render."""


class FinishError(AudioError):
    """Finishing failed: loudness could not be measured, or a speaker has no display name."""
