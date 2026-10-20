"""Episode assembly: a pure timeline (EDL) built from measured asset durations, rendered by the ffmpeg runner."""
from .timeline import Timeline, TimelineInput, build_timeline

__all__ = ["Timeline", "TimelineInput", "build_timeline"]
