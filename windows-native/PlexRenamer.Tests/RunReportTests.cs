using System;
using PlexRenamer.Bridge;
using PlexRenamer.Views;
using Xunit;

namespace PlexRenamer.Tests;

public class RunReportTests
{
    [StaFact]
    public void RunReport_StartsHiddenWithUndoDisabled()
    {
        var widget = new PlexRenamer.Views.RunReport();
        Assert.Equal(System.Windows.Visibility.Collapsed, widget.Visibility);
        Assert.Null(widget.LastJournalPath);
    }

    [StaFact]
    public void RunReport_Show_EnablesUndoWhenJournalPathPresent()
    {
        var widget = new PlexRenamer.Views.RunReport();
        var report = new PlexRenamer.Bridge.RunReport
        {
            Succeeded = 5,
            Failed = 0,
            Skipped = 1,
            CleanupRan = false,
            JournalPath = "/abs/journal.json",
        };
        widget.Show(report);
        Assert.Equal(System.Windows.Visibility.Visible, widget.Visibility);
        Assert.Equal("/abs/journal.json", widget.LastJournalPath);
    }

    [StaFact]
    public void RunReport_Show_DisablesUndoWhenJournalPathNull()
    {
        var widget = new PlexRenamer.Views.RunReport();
        var report = new PlexRenamer.Bridge.RunReport
        {
            Succeeded = 0,
            Failed = 3,
            Skipped = 0,
            CleanupRan = false,
            JournalPath = null,
        };
        widget.Show(report);
        Assert.Equal(System.Windows.Visibility.Visible, widget.Visibility);
        Assert.Null(widget.LastJournalPath);
    }

    [StaFact]
    public void RunReport_Clear_HidesAndDisablesUndo()
    {
        var widget = new PlexRenamer.Views.RunReport();
        widget.Show(new PlexRenamer.Bridge.RunReport
        {
            Succeeded = 1, Failed = 0, Skipped = 0, CleanupRan = false,
            JournalPath = "/abs/j.json",
        });
        widget.Clear();
        Assert.Equal(System.Windows.Visibility.Collapsed, widget.Visibility);
        Assert.Null(widget.LastJournalPath);
    }
}
