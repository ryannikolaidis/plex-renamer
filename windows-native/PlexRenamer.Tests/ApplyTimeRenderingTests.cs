using System.Collections.Generic;
using System.Windows;
using PlexRenamer.Bridge.Schemas;
using PlexRenamer.Views;
using Xunit;

namespace PlexRenamer.Tests;

/// <summary>
/// [StaFact] rendering tests for the apply-time dialogs landing in slice
/// 4. Same Measure+Arrange discipline as slice 2's RenderingTests +
/// slice 3's ResolveTimeRenderingTests: construct, force layout on the
/// inner content (FluentWindow tops don't get ActualWidth from synthetic
/// layout; the inner Content does), assert non-zero ActualWidth/Height.
/// </summary>
public class ApplyTimeRenderingTests
{
    private static void MeasureAndArrange(FrameworkElement element, Size size)
    {
        element.Measure(size);
        element.Arrange(new Rect(new Point(0, 0), size));
        element.UpdateLayout();
    }

    [StaFact]
    public void CollisionReviewDialog_ContentRendersWithNonZeroDimensions()
    {
        var dialog = new CollisionReviewDialog(new List<Collision>
        {
            new()
            {
                Target = @"C:\Movies\Foo (2020)\Foo (2020).mkv",
                Sources = new[] { @"C:\src\a.mkv", @"C:\src\b.mkv" },
                Reason = "duplicate_input",
            }
        });
        Assert.Equal(720, dialog.Width);
        if (dialog.Content is FrameworkElement content)
        {
            MeasureAndArrange(content, new Size(688, 512));
            Assert.True(content.ActualWidth > 0, "CollisionReviewDialog content width must be non-zero.");
            Assert.True(content.ActualHeight > 0, "CollisionReviewDialog content height must be non-zero.");
        }
        else
        {
            Assert.Fail("CollisionReviewDialog.Content was not a FrameworkElement.");
        }
    }

    [StaFact]
    public void CleanupConfirmModal_ContentRendersWithNonZeroDimensions()
    {
        var modal = new CleanupConfirmModal(new List<string> { @"C:\src\a.mkv", @"C:\src" });
        Assert.Equal(700, modal.Width);
        if (modal.Content is FrameworkElement content)
        {
            MeasureAndArrange(content, new Size(668, 512));
            Assert.True(content.ActualWidth > 0, "CleanupConfirmModal content width must be non-zero.");
            Assert.True(content.ActualHeight > 0, "CleanupConfirmModal content height must be non-zero.");
        }
        else
        {
            Assert.Fail("CleanupConfirmModal.Content was not a FrameworkElement.");
        }
    }

    [StaFact]
    public void RunReport_VisibleWithReportRendersNonZero()
    {
        var widget = new PlexRenamer.Views.RunReport();
        widget.Show(new PlexRenamer.Bridge.RunReport
        {
            Succeeded = 5, Failed = 0, Skipped = 1, CleanupRan = false,
            JournalPath = "/abs/j.json",
        });
        MeasureAndArrange(widget, new Size(1200, 240));
        Assert.True(widget.ActualWidth > 0, "RunReport width must be non-zero after Show + layout.");
        Assert.True(widget.ActualHeight > 0, "RunReport height must be non-zero after Show + layout.");
    }
}
