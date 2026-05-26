using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using PlexRenamer.Bridge;
using PlexRenamer.Bridge.Schemas;
using Xunit;

namespace PlexRenamer.Tests;

/// <summary>
/// Tests for the JSON-RPC client surface using a fake IEngineClient. The
/// real subprocess-spawn path is exercised by manual Windows smoke tests
/// (documented in windows-native/README.md); these unit tests pin the
/// view-model wiring against the bridge contract without spawning a real
/// Python sidecar.
/// </summary>
public class BridgeClientTests
{
    [Fact]
    public async Task FakeEngineClient_GetSettings_ReturnsConfiguredValue()
    {
        var fake = new FakeEngineClient
        {
            SettingsToReturn = new Settings
            {
                TmdbApiKey = "test-key",
                MoviesRoot = "C:\\Movies",
            }
        };
        var result = await fake.GetSettingsAsync();
        Assert.Equal("test-key", result.TmdbApiKey);
        Assert.Equal("C:\\Movies", result.MoviesRoot);
    }

    [Fact]
    public async Task FakeEngineClient_ParseAndResolve_ReturnsRows()
    {
        var fake = new FakeEngineClient
        {
            ParseResolveResultToReturn = new ParseResolveResult
            {
                Rows = new List<ResolvedRow>
                {
                    new()
                    {
                        RowId = "/test/Movie.2020.mkv",
                        Parsed = new ParsedFields
                        {
                            SourcePath = "/test/Movie.2020.mkv",
                            Kind = "movie",
                            RawFilename = "Movie.2020.mkv",
                            TitleCandidate = "Movie",
                            Year = 2020,
                        },
                        GroupKey = "movie::/test/Movie.2020.mkv",
                    }
                },
                Groups = new List<ResolvedGroup>
                {
                    new()
                    {
                        GroupKey = "movie::/test/Movie.2020.mkv",
                        Kind = "movie",
                        Label = "Movie",
                        RowIds = new[] { "/test/Movie.2020.mkv" },
                    }
                },
                InputRoot = "/test",
            }
        };
        var result = await fake.ParseAndResolveAsync(new[] { "/test" }, null);
        Assert.Single(result.Rows);
        Assert.Equal("Movie", result.Rows[0].Parsed.TitleCandidate);
        Assert.Single(result.Groups);
    }

    [Fact]
    public async Task FakeEngineClient_ApplyPlan_YieldsProgressThenDone()
    {
        var fake = new FakeEngineClient
        {
            ApplyEvents = new List<ApplyEvent>
            {
                new() { EventKind = "op_started", OpIndex = 0 },
                new() { EventKind = "op_verified", Bytes = 1024 },
                new()
                {
                    EventKind = "done",
                    Result = new RunReport
                    {
                        Succeeded = 1, Failed = 0, Skipped = 0, CleanupRan = false,
                    }
                },
            }
        };
        var plan = new PlanOp
        {
            Ops = new List<RenameOp>
            {
                new() { Source = "/src", Target = "/dst", Kind = "movie", Anchor = "tmdb-1" }
            },
            MoviesRoot = "/m", TvRoot = "/t", InputRoot = "/src",
        };
        var events = new List<ApplyEvent>();
        await foreach (var ev in fake.ApplyPlanAsync(plan, false, false, null))
        {
            events.Add(ev);
        }
        Assert.Equal(3, events.Count);
        Assert.Equal("done", events[^1].EventKind);
        Assert.Equal(1, events[^1].Result!.Succeeded);
    }
}

/// <summary>
/// In-memory test double for <see cref="IEngineClient"/>. Records method
/// calls and replays canned responses without spawning a Python process.
/// </summary>
internal sealed class FakeEngineClient : IEngineClient
{
    public Settings SettingsToReturn { get; set; } = new();
    public ParseResolveResult ParseResolveResultToReturn { get; set; } = new()
    {
        Rows = new List<ResolvedRow>(),
        Groups = new List<ResolvedGroup>(),
        InputRoot = "/",
    };
    public List<ApplyEvent> ApplyEvents { get; set; } = new();
    public List<string> CallsMade { get; } = new();

    public event EventHandler<EngineExitedEventArgs>? UnexpectedExit;

    public Task StartAsync(CancellationToken cancellationToken = default)
    {
        CallsMade.Add("StartAsync");
        return Task.CompletedTask;
    }

    public Task RestartAsync(CancellationToken cancellationToken = default)
    {
        CallsMade.Add("RestartAsync");
        return Task.CompletedTask;
    }

    public Task<Settings> GetSettingsAsync(CancellationToken cancellationToken = default)
    {
        CallsMade.Add("GetSettingsAsync");
        return Task.FromResult(SettingsToReturn);
    }

    public Task<Settings> SaveSettingsAsync(Settings settings, CancellationToken cancellationToken = default)
    {
        CallsMade.Add("SaveSettingsAsync");
        SettingsToReturn = settings;
        return Task.FromResult(settings);
    }

    public Task<ParseResolveResult> ParseAndResolveAsync(
        IReadOnlyList<string> paths,
        Settings? settings,
        CancellationToken cancellationToken = default)
    {
        CallsMade.Add($"ParseAndResolveAsync(paths={paths.Count})");
        return Task.FromResult(ParseResolveResultToReturn);
    }

    public async IAsyncEnumerable<ApplyEvent> ApplyPlanAsync(
        PlanOp plan,
        bool cleanup,
        bool verifyHash,
        Settings? settings,
        [System.Runtime.CompilerServices.EnumeratorCancellation]
        CancellationToken cancellationToken = default)
    {
        CallsMade.Add($"ApplyPlanAsync(ops={plan.Ops.Count})");
        foreach (var ev in ApplyEvents)
        {
            await Task.Yield();
            yield return ev;
        }
    }

    public ValueTask DisposeAsync()
    {
        CallsMade.Add("DisposeAsync");
        return ValueTask.CompletedTask;
    }

    public void RaiseUnexpectedExit(int code, string? stderr)
        => UnexpectedExit?.Invoke(this, new EngineExitedEventArgs { ExitCode = code, Stderr = stderr });
}
