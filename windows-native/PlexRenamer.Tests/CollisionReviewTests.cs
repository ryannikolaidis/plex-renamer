using System.Collections.Generic;
using PlexRenamer.Bridge.Schemas;
using PlexRenamer.Views;
using Xunit;

namespace PlexRenamer.Tests;

public class CollisionReviewTests
{
    [StaFact]
    public void CollisionReviewDialog_NoSkipChosen_BeforeClickReturnsEmpty()
    {
        var collisions = new List<Collision>
        {
            new()
            {
                Target = @"C:\Movies\Foo (2020) {tmdb-1}\Foo (2020) {tmdb-1}.mkv",
                Sources = new[] { @"C:\src\a.mkv", @"C:\src\b.mkv" },
                Reason = "duplicate_input",
            }
        };
        var dialog = new CollisionReviewDialog(collisions);
        Assert.Empty(dialog.SkippedSourcePaths);
        Assert.Single(dialog.Collisions);
    }
}
