using System.Collections.Generic;
using System.Threading.Tasks;
using PlexRenamer.Bridge.Schemas;
using Xunit;

namespace PlexRenamer.Tests;

/// <summary>
/// Tests the bridge surface the edit pane uses: search_tmdb_free,
/// find_by_imdb, edit_row. The pane's actual XAML wiring is exercised
/// via the rendering test in ResolveTimeRenderingTests; here we pin
/// the engine-side contract via the FakeEngineClient double.
/// </summary>
public class EditPaneTests
{
    [Fact]
    public async Task FakeEngineClient_SearchTmdbFree_RoundsTripsCandidates()
    {
        var fake = new FakeEngineClient
        {
            TmdbSearchResultToReturn = new TmdbSearchResult
            {
                Candidates = new List<Candidate>
                {
                    new()
                    {
                        AnchorKind = "tmdb", AnchorId = "603", Kind = "movie",
                        Title = "The Matrix", Year = 1999, Confidence = 0.7,
                    }
                },
            },
        };
        var result = await fake.SearchTmdbFreeAsync("Matrix", "any", settings: null);
        Assert.Single(result.Candidates);
        Assert.Equal("The Matrix", result.Candidates[0].Title);
    }

    [Fact]
    public async Task FakeEngineClient_FindByImdb_TmdbHit_ReturnsTmdbAnchoredCandidate()
    {
        var fake = new FakeEngineClient
        {
            FindByImdbResultToReturn = new FindByImdbResult
            {
                Candidate = new Candidate
                {
                    AnchorKind = "tmdb", AnchorId = "603", Kind = "movie",
                    Title = "The Matrix", Year = 1999, Confidence = 0.8,
                },
            },
        };
        var row = MakeRow();
        var result = await fake.FindByImdbAsync("tt0133093", row, settings: null);
        Assert.Equal("tmdb", result.Candidate.AnchorKind);
        Assert.Equal("603", result.Candidate.AnchorId);
    }

    [Fact]
    public async Task FakeEngineClient_FindByImdb_NoCanned_SynthesizesImdbAnchor()
    {
        var fake = new FakeEngineClient(); // FindByImdbResultToReturn is null
        var row = MakeRow(title: "Unknown Film", year: 1999);
        var result = await fake.FindByImdbAsync("tt9999999", row, settings: null);
        // The Qt path's resolve_imdb_for_row behavior: TMDB miss
        // synthesizes an imdb-anchored Candidate at confidence 0.55. The
        // fake here mirrors that semantic so MainWindow's wiring tests
        // against the real shape.
        Assert.Equal("imdb", result.Candidate.AnchorKind);
        Assert.Equal("tt9999999", result.Candidate.AnchorId);
    }

    [Fact]
    public async Task FakeEngineClient_EditRow_RoundsTripsUpdatedRows()
    {
        var fake = new FakeEngineClient
        {
            EditRowResultToReturn = new EditRowResult
            {
                Rows = new List<ResolvedRow> { MakeRow(title: "Foo (Edited)") },
            },
        };
        var overrides = new EditRowOverrides
        {
            ManualTitle = "Foo (Edited)",
            Skip = false,
        };
        var result = await fake.EditRowAsync(
            new List<ResolvedRow> { MakeRow() },
            rowId: "/test/Foo.2020.mkv",
            overrides,
            settings: null);
        Assert.Single(result.Rows);
        Assert.Equal("Foo (Edited)", result.Rows[0].Parsed.TitleCandidate);
    }

    private static ResolvedRow MakeRow(string title = "Foo", int year = 2020)
    {
        return new ResolvedRow
        {
            RowId = "/test/Foo.2020.mkv",
            Parsed = new ParsedFields
            {
                SourcePath = "/test/Foo.2020.mkv",
                Kind = "movie",
                RawFilename = "Foo.2020.mkv",
                TitleCandidate = title,
                Year = year,
            },
            GroupKey = "movie::/test/Foo.2020.mkv",
        };
    }
}
