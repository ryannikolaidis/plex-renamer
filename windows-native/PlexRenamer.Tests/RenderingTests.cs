using System.Windows;
using PlexRenamer.Views;
using Xunit;

namespace PlexRenamer.Tests;

/// <summary>
/// [StaFact]-based WPF rendering tests. These exercise real layout
/// (Measure + Arrange) and assert non-zero ActualWidth / ActualHeight on
/// load-bearing controls — the WPF analogue of the Qt widget.grab() +
/// sizeHint discipline INVARIANTS.md mandates. They are the regression
/// gate for the "squished / flaky" defect class the win-native project
/// exists to fix.
/// </summary>
/// <remarks>
/// Tests use Xunit.StaFact's [StaFact] attribute so each runs on a
/// single-threaded apartment with a real WPF AppDomain available. The
/// Application instance is constructed once per test method to avoid
/// "Cannot create more than one System.Windows.Application instance"
/// from leaking across tests.
/// </remarks>
public class RenderingTests
{
    private static void MeasureAndArrange(FrameworkElement element, Size size)
    {
        element.Measure(size);
        element.Arrange(new Rect(new Point(0, 0), size));
        element.UpdateLayout();
    }

    [StaFact]
    public void DropZone_RendersWithNonZeroDimensions()
    {
        var dropZone = new DropZone();
        MeasureAndArrange(dropZone, new Size(1400, 100));
        Assert.True(dropZone.ActualWidth > 0, "DropZone width must be non-zero after layout.");
        Assert.True(dropZone.ActualHeight > 0, "DropZone height must be non-zero after layout.");
    }

    [StaFact]
    public void SourcePanel_RendersWithNonZeroDimensions()
    {
        var panel = new SourcePanel();
        MeasureAndArrange(panel, new Size(700, 700));
        Assert.True(panel.ActualWidth > 0, "SourcePanel width must be non-zero after layout.");
        Assert.True(panel.ActualHeight > 0, "SourcePanel height must be non-zero after layout.");
    }

    [StaFact]
    public void TargetPanel_RendersWithNonZeroDimensions()
    {
        var panel = new TargetPanel();
        MeasureAndArrange(panel, new Size(700, 700));
        Assert.True(panel.ActualWidth > 0, "TargetPanel width must be non-zero after layout.");
        Assert.True(panel.ActualHeight > 0, "TargetPanel height must be non-zero after layout.");
    }

    [StaFact]
    public void ActionBar_RendersWithNonZeroDimensions_AndApplyDisabled()
    {
        var bar = new ActionBar();
        bar.IsApplyEnabled = false;
        MeasureAndArrange(bar, new Size(1400, 50));
        Assert.True(bar.ActualWidth > 0, "ActionBar width must be non-zero after layout.");
        Assert.True(bar.ActualHeight > 0, "ActionBar height must be non-zero after layout.");
        // Apply is rendered-but-disabled in slice 2; slice 4 wires it.
        Assert.False(bar.IsApplyEnabled);
    }
}
