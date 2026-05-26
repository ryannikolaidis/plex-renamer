using System.Collections.Generic;
using PlexRenamer.Views;
using Xunit;

namespace PlexRenamer.Tests;

public class CleanupConfirmTests
{
    [StaFact]
    public void CleanupConfirmModal_Constructs_WithPathsList()
    {
        var paths = new List<string>
        {
            @"C:\src\a.mkv",
            @"C:\src\b.en.srt",
            @"C:\src",  // parent dir
        };
        var modal = new CleanupConfirmModal(paths);
        Assert.Equal(700, modal.Width);
        Assert.Equal(560, modal.Height);
    }
}
