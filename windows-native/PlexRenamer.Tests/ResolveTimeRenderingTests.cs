using System.Windows;
using PlexRenamer.Views;
using Xunit;

namespace PlexRenamer.Tests;

/// <summary>
/// [StaFact] rendering tests for the resolve-time dialogs landing in
/// slice 3. Same discipline as slice 2's RenderingTests: construct,
/// force layout via Measure+Arrange on the inner content, assert
/// non-zero ActualWidth/Height. Catches "squished" rendering
/// regressions on the views slice 3 ships (ConfidenceBadge,
/// StatusFooter as UserControls; ShowAnchorPicker, EditPane,
/// TmdbKeyPrompt as FluentWindows where we measure the inner Content).
/// </summary>
public class ResolveTimeRenderingTests
{
    private static void MeasureAndArrange(FrameworkElement element, Size size)
    {
        element.Measure(size);
        element.Arrange(new Rect(new Point(0, 0), size));
        element.UpdateLayout();
    }

    [StaFact]
    public void ConfidenceBadge_RendersWithNonZeroDimensions_Green()
    {
        var badge = new ConfidenceBadge { Confidence = 0.95 };
        MeasureAndArrange(badge, new Size(120, 30));
        Assert.True(badge.ActualWidth > 0, "ConfidenceBadge width must be non-zero after layout.");
        Assert.True(badge.ActualHeight > 0, "ConfidenceBadge height must be non-zero after layout.");
    }

    [StaFact]
    public void ConfidenceBadge_RendersWithNonZeroDimensions_Yellow()
    {
        var badge = new ConfidenceBadge { Confidence = 0.70 };
        MeasureAndArrange(badge, new Size(120, 30));
        Assert.True(badge.ActualWidth > 0);
        Assert.True(badge.ActualHeight > 0);
    }

    [StaFact]
    public void ConfidenceBadge_RendersWithNonZeroDimensions_Red()
    {
        var badge = new ConfidenceBadge { Confidence = 0.40 };
        MeasureAndArrange(badge, new Size(120, 30));
        Assert.True(badge.ActualWidth > 0);
        Assert.True(badge.ActualHeight > 0);
    }

    [StaFact]
    public void StatusFooter_RendersWithNonZeroDimensions()
    {
        var control = new StatusFooter();
        control.SetLibraryRoots(@"C:\Movies", @"C:\TV");
        control.SetEngineState(EngineState.Ready);
        MeasureAndArrange(control, new Size(900, 32));
        Assert.True(control.ActualWidth > 0, "StatusFooter width must be non-zero after layout.");
        Assert.True(control.ActualHeight > 0, "StatusFooter height must be non-zero after layout.");
    }

    [StaFact]
    public void TmdbKeyPrompt_ContentRendersWithNonZeroDimensions()
    {
        var prompt = new TmdbKeyPrompt();
        Assert.Equal(480, prompt.Width);
        Assert.Equal(280, prompt.Height);
        if (prompt.Content is FrameworkElement content)
        {
            MeasureAndArrange(content, new Size(448, 232));
            Assert.True(content.ActualWidth > 0);
            Assert.True(content.ActualHeight > 0);
        }
        else
        {
            Assert.Fail("TmdbKeyPrompt.Content not a FrameworkElement.");
        }
    }
}
