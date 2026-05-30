using System.IO;
using System.Windows;
using System.Windows.Controls;
using PlexRenamer.Bridge;

namespace PlexRenamer.Views;

/// <summary>
/// In-flight apply progress widget. Lives above the RunReport area and
/// is visible only while apply_plan is streaming events. Renders:
/// op N of M / progress bar / current source filename. The widget is
/// passive — MainWindow.OnApplyClicked drives it via <see cref="Begin"/>,
/// <see cref="UpdateForEvent"/>, and <see cref="Hide"/>.
/// </summary>
public partial class ApplyProgress : UserControl
{
    public ApplyProgress()
    {
        InitializeComponent();
    }

    /// <summary>Show the widget at zero progress before the first event arrives.</summary>
    public void Begin(int totalOps)
    {
        HeadlineText.Text = "Applying...";
        CountText.Text = totalOps > 0 ? $"0 / {totalOps}" : "starting...";
        Bar.Maximum = totalOps > 0 ? totalOps : 1;
        Bar.Value = 0;
        CurrentFileText.Text = string.Empty;
        Visibility = Visibility.Visible;
    }

    /// <summary>
    /// Update visible state from one apply_plan progress event. The
    /// widget shows op_started transitions live so the user sees the
    /// current file's name as soon as its copy begins.
    /// </summary>
    public void UpdateForEvent(ApplyEvent ev)
    {
        if (ev.TotalOps is int total && total > 0)
        {
            Bar.Maximum = total;
        }
        switch (ev.EventKind)
        {
            case "op_started":
                if (ev.OpIndex is int started)
                {
                    Bar.Value = started;
                    CountText.Text = ev.TotalOps is int t ? $"{started + 1} / {t}" : $"{started + 1}";
                }
                CurrentFileText.Text = ev.Source is { Length: > 0 } src
                    ? "Copying " + Path.GetFileName(src)
                    : string.Empty;
                break;
            case "op_verified":
                if (ev.OpIndex is int verified)
                {
                    Bar.Value = verified + 1;
                    CountText.Text = ev.TotalOps is int t2 ? $"{verified + 1} / {t2}" : $"{verified + 1}";
                }
                break;
            case "op_failed":
                if (ev.OpIndex is int failed)
                {
                    Bar.Value = failed + 1;
                    CountText.Text = ev.TotalOps is int t3 ? $"{failed + 1} / {t3}" : $"{failed + 1}";
                }
                CurrentFileText.Text = ev.Error is { Length: > 0 } err
                    ? $"Failed: {err}"
                    : CurrentFileText.Text;
                break;
        }
    }

    public void Hide()
    {
        Visibility = Visibility.Collapsed;
    }
}
