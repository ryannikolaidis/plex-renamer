using System;
using System.Collections.Generic;
using System.Threading.Tasks;
using PlexRenamer.Bridge.Schemas;
using PlexRenamer.Views;
using Xunit;

namespace PlexRenamer.Tests;

/// <summary>
/// Tests for the show-anchor picker dialog. Verifies the picker drives
/// the engine's iterate_anchor_search RPC and constructs canonical
/// "View on TMDB" / "View on IMDb" URLs from the daemon-returned
/// Candidate.AnchorKind + Candidate.AnchorId.
/// </summary>
public class ShowAnchorPickerTests
{
    [StaFact]
    public void CandidateView_TmdbMovie_PointsAtThemoviedbOrgMoviePath()
    {
        var candidate = new Candidate
        {
            AnchorKind = "tmdb",
            AnchorId = "603",
            Kind = "movie",
            Title = "The Matrix",
            Year = 1999,
            Confidence = 0.92,
        };
        var view = new CandidateView(candidate);
        Assert.Equal("View on TMDB", view.ExternalUrlLabel);
        Assert.Equal("https://www.themoviedb.org/movie/603", view.ExternalUrl.AbsoluteUri);
    }

    [StaFact]
    public void CandidateView_TmdbTv_PointsAtThemoviedbOrgTvPath()
    {
        var candidate = new Candidate
        {
            AnchorKind = "tmdb",
            AnchorId = "42",
            Kind = "tv",
            Title = "Lazarus",
            Year = 2024,
            Confidence = 0.95,
        };
        var view = new CandidateView(candidate);
        Assert.Equal("View on TMDB", view.ExternalUrlLabel);
        Assert.Equal("https://www.themoviedb.org/tv/42", view.ExternalUrl.AbsoluteUri);
    }

    [StaFact]
    public void CandidateView_ImdbAnchor_PointsAtImdbTitlePath()
    {
        var candidate = new Candidate
        {
            AnchorKind = "imdb",
            AnchorId = "tt9999999",
            Kind = "movie",
            Title = "Unknown",
            Year = 1999,
            Confidence = 0.55,
        };
        var view = new CandidateView(candidate);
        Assert.Equal("View on IMDb", view.ExternalUrlLabel);
        Assert.Equal("https://www.imdb.com/title/tt9999999/", view.ExternalUrl.AbsoluteUri);
    }

    [Fact]
    public async Task FakeEngineClient_IterateAnchorSearch_RoundsTripsVariantSignals()
    {
        var fake = new FakeEngineClient
        {
            AnchorSearchResultToReturn = new AnchorSearchResult
            {
                Candidates = new List<Candidate>
                {
                    new()
                    {
                        AnchorKind = "tmdb", AnchorId = "42", Kind = "tv",
                        Title = "Lazarus", Year = 2024, Confidence = 0.9,
                    }
                },
                VariantUsed = "Lazarus",
                VariantOriginal = "Lazarus_2",
                VariantsTried = new[] { "Lazarus_2", "Lazarus" },
            },
        };
        var result = await fake.IterateAnchorSearchAsync("Lazarus_2", year: null, settings: null);
        Assert.Single(result.Candidates);
        Assert.Equal("Lazarus", result.VariantUsed);
        Assert.Equal("Lazarus_2", result.VariantOriginal);
    }

    [Fact]
    public async Task FakeEngineClient_SelectAnchor_RoundsTripsUpdatedRows()
    {
        var fake = new FakeEngineClient
        {
            SelectAnchorResultToReturn = new SelectAnchorResult
            {
                Rows = new List<ResolvedRow>
                {
                    new()
                    {
                        RowId = "/x", GroupKey = "tv::Lazarus",
                        Parsed = new ParsedFields
                        {
                            SourcePath = "/x", Kind = "tv", RawFilename = "x.mkv",
                        },
                        Candidate = new Candidate
                        {
                            AnchorKind = "tmdb", AnchorId = "42", Kind = "tv",
                            Title = "Lazarus", Confidence = 0.95,
                        },
                    }
                },
            },
        };
        var picked = new Candidate
        {
            AnchorKind = "tmdb", AnchorId = "42", Kind = "tv",
            Title = "Lazarus", Confidence = 0.95,
        };
        var result = await fake.SelectAnchorAsync(
            new List<ResolvedRow>(), "tv::Lazarus", picked, settings: null);
        Assert.Single(result.Rows);
        Assert.Equal("42", result.Rows[0].Candidate!.AnchorId);
    }
}
