"""TheTVDB v4 client + cache.

We use TVDB primarily as an alternative episode-ordering source for
TV shows when TMDB's ordering disagrees with the user's expectation.
TVDB exposes multiple orderings per show (default / official / DVD /
absolute / alternate / regional), which is exactly the disambiguation
some shows need.

The TMDB anchor on the show folder stays the user's primary choice;
``{tmdb-X}`` and ``{tvdb-X}`` are both valid Plex anchors and the
episode source is an orthogonal decision.
"""

from plex_renamer.tvdb.client import (
    TVDBClient,
    TVDBSeasonType,
    TVDBSeriesEpisodes,
    TVDBSeriesResult,
)

__all__ = [
    "TVDBClient",
    "TVDBSeasonType",
    "TVDBSeriesEpisodes",
    "TVDBSeriesResult",
]
