using System.Collections.Generic;
using System.Threading.Tasks;
using PlexRenamer.Bridge.Schemas;
using Xunit;

namespace PlexRenamer.Tests;

/// <summary>
/// End-to-end tests of the apply flow's RPC contract against the
/// FakeEngineClient: build_plan → apply_plan streaming → undo_batch.
/// The MainWindow wiring that orchestrates these calls is exercised via
/// the rendering tests in ApplyTimeRenderingTests; here we pin the
/// engine-side contract for each step independently.
/// </summary>
public class ApplyFlowEndToEndTests
{
    [Fact]
    public async Task FakeEngineClient_BuildPlan_RoundsTripsCollisions()
    {
        var fake = new FakeEngineClient
        {
            BuildPlanResultToReturn = new BuildPlanResult
            {
                Plan = new PlanOp
                {
                    Ops = new List<RenameOp>(),
                    Collisions = new List<Collision>
                    {
                        new()
                        {
                            Target = @"C:\Movies\Foo (2020)\Foo (2020).mkv",
                            Sources = new[] { @"C:\src\a.mkv", @"C:\src\b.mkv" },
                            Reason = "duplicate_input",
                        }
                    },
                    MoviesRoot = @"C:\Movies",
                    TvRoot = @"C:\TV",
                    InputRoot = @"C:\src",
                },
            },
        };
        var result = await fake.BuildPlanAsync(
            new List<ResolvedRow>(), inputRoot: null, applyEditions: false, settings: null);
        Assert.Single(result.Plan.Collisions);
        Assert.Equal("duplicate_input", result.Plan.Collisions[0].Reason);
    }

    [Fact]
    public async Task FakeEngineClient_ApplyPlan_StreamsToDoneEvent_WithRunReport()
    {
        var fake = new FakeEngineClient
        {
            ApplyEvents = new List<PlexRenamer.Bridge.ApplyEvent>
            {
                new() { EventKind = "op_started", OpIndex = 0, Source = "/a", Target = "/x" },
                new() { EventKind = "op_verified", Bytes = 1024, Source = "/a", Target = "/x" },
                new()
                {
                    EventKind = "done",
                    Result = new PlexRenamer.Bridge.RunReport
                    {
                        Succeeded = 1, Failed = 0, Skipped = 0, CleanupRan = false,
                        JournalPath = "/abs/j.json",
                    },
                },
            },
        };
        var plan = new PlanOp
        {
            Ops = new List<RenameOp>
            {
                new() { Source = "/a", Target = "/x", Kind = "movie", Anchor = "tmdb-1" }
            },
            MoviesRoot = "/m", TvRoot = "/t", InputRoot = "/in",
        };
        PlexRenamer.Bridge.RunReport? finalReport = null;
        var events = new List<PlexRenamer.Bridge.ApplyEvent>();
        await foreach (var ev in fake.ApplyPlanAsync(plan, cleanup: false, verifyHash: false, settings: null))
        {
            events.Add(ev);
            if (ev.EventKind == "done")
            {
                finalReport = ev.Result;
            }
        }
        Assert.Equal(3, events.Count);
        Assert.NotNull(finalReport);
        Assert.Equal(1, finalReport!.Succeeded);
        Assert.Equal("/abs/j.json", finalReport.JournalPath);
    }

    [Fact]
    public async Task FakeEngineClient_UndoBatch_RoundsTripsUndoReport()
    {
        var fake = new FakeEngineClient
        {
            UndoReportToReturn = new UndoReport
            {
                Reverted = 5,
                MovedToReview = 0,
                ReviewDir = null,
                SourcesRecoverable = true,
            },
        };
        var report = await fake.UndoBatchAsync("/abs/j.json");
        Assert.Equal(5, report.Reverted);
        Assert.True(report.SourcesRecoverable);
    }
}
