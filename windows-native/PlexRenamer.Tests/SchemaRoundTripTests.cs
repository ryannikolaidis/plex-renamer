using System.Text.Json;
using PlexRenamer.Bridge.Schemas;
using Xunit;

namespace PlexRenamer.Tests;

/// <summary>
/// JSON round-trip tests for the wire-shape POCO records. These pin the
/// JSON-RPC contract end-to-end: the records must serialize to the
/// snake_case keys the daemon documents in `docs/win-native-bridge.md`,
/// and the daemon's emitted JSON must deserialize back into the records
/// without dropping or renaming any field.
/// </summary>
public class SchemaRoundTripTests
{
    private static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
    };

    [Fact]
    public void Settings_RoundTrips_WithSnakeCaseKeys()
    {
        var original = new Settings
        {
            TmdbApiKey = "abc",
            OmdbApiKey = "def",
            MoviesRoot = @"C:\Movies",
            TvRoot = @"C:\TV",
            CleanupEnabled = true,
            AutoAcceptTopHit = false,
        };
        var json = JsonSerializer.Serialize(original, Options);
        // Verify wire keys are snake_case (matches the daemon contract).
        Assert.Contains("\"tmdb_api_key\":", json);
        Assert.Contains("\"movies_root\":", json);
        Assert.Contains("\"cleanup_enabled\":", json);
        Assert.Contains("\"auto_accept_top_hit\":", json);
        // Round-trip back to a record and confirm every field survived.
        var decoded = JsonSerializer.Deserialize<Settings>(json, Options);
        Assert.NotNull(decoded);
        Assert.Equal(original, decoded);
    }

    [Fact]
    public void Episode_RoundTrips_WithCanonicalEpisodeKey()
    {
        // The Episode record cannot have a property literally named
        // "Episode" (CS0542 conflicts with the type name). The C# property
        // is `Number` with [JsonPropertyName("episode")] so the wire key
        // matches the daemon's emitted JSON. This test verifies the
        // attribute is present and effective; a regression would mean
        // every TV resolve flow silently drops the episode number.
        const string daemonEmittedJson = """
            {"season":1,"episode":4,"title":"Pilot","air_date":"2020-09-25"}
            """;
        var decoded = JsonSerializer.Deserialize<Episode>(daemonEmittedJson, Options);
        Assert.NotNull(decoded);
        Assert.Equal(1, decoded.Season);
        Assert.Equal(4, decoded.Number);
        Assert.Equal("Pilot", decoded.Title);
        Assert.Equal("2020-09-25", decoded.AirDate);

        // And the reverse: serializing should produce "episode", not
        // "number" or "episode_".
        var serialized = JsonSerializer.Serialize(decoded, Options);
        Assert.Contains("\"episode\":4", serialized);
        Assert.DoesNotContain("\"number\":", serialized);
        Assert.DoesNotContain("\"episode_\":", serialized);
    }

    [Fact]
    public void Candidate_RoundTrips_WithEpisodeList()
    {
        // The Candidate record carries an EpisodeList; this round-trip
        // also exercises the Episode JsonPropertyName fix above on every
        // nested element.
        const string daemonEmittedJson = """
            {
              "anchor_kind": "tmdb",
              "anchor_id": "12345",
              "kind": "tv",
              "title": "Foo",
              "year": 2018,
              "confidence": 0.92,
              "episode_list": [
                {"season": 1, "episode": 1, "title": "Pilot", "air_date": null},
                {"season": 1, "episode": 2, "title": "Second", "air_date": null}
              ]
            }
            """;
        var decoded = JsonSerializer.Deserialize<Candidate>(daemonEmittedJson, Options);
        Assert.NotNull(decoded);
        Assert.Equal("tmdb", decoded.AnchorKind);
        Assert.Equal("12345", decoded.AnchorId);
        Assert.Equal(2, decoded.EpisodeList.Count);
        Assert.Equal(1, decoded.EpisodeList[0].Number);
        Assert.Equal("Pilot", decoded.EpisodeList[0].Title);
        Assert.Equal(2, decoded.EpisodeList[1].Number);
    }

    [Fact]
    public void ParseResolveResult_RoundTrips_WithFlatErrorsAndInputRoot()
    {
        // The wire shape is "rows + groups + input_root + errors" flat at
        // the top level (no outer envelope). This pins the doc claim.
        const string daemonEmittedJson = """
            {
              "rows": [],
              "groups": [],
              "input_root": "/abs/parent",
              "errors": [
                {"source_path": "/abs/parent/file.mkv", "message": "TMDB 503"}
              ]
            }
            """;
        var decoded = JsonSerializer.Deserialize<ParseResolveResult>(daemonEmittedJson, Options);
        Assert.NotNull(decoded);
        Assert.Empty(decoded.Rows);
        Assert.Empty(decoded.Groups);
        Assert.Equal("/abs/parent", decoded.InputRoot);
        Assert.Single(decoded.Errors);
        Assert.Equal("/abs/parent/file.mkv", decoded.Errors[0].SourcePath);
    }
}
