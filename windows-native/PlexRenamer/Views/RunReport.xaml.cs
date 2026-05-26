using System;
using System.Windows;
using System.Windows.Controls;
using BridgeRunReport = PlexRenamer.Bridge.RunReport;

namespace PlexRenamer.Views;

/// <summary>
/// Post-apply summary widget. Shows the daemon-emitted RunReport
/// (succeeded/failed/skipped counts + per-row error messages) plus an
/// Undo button bound to undo_batch via the parent MainWindow.
/// </summary>
public partial class RunReport : UserControl
{
    /// <summary>The journal path of the last applied batch, or null if no apply has run.</summary>
    public string? LastJournalPath { get; private set; }

    /// <summary>Fired when the Undo button is clicked. Carries the journal path.</summary>
    public event EventHandler<string>? UndoRequested;

    public RunReport()
    {
        InitializeComponent();
        Clear();
    }

    /// <summary>
    /// Render the report. If the daemon returned a journal_path, enable
    /// the Undo button.
    /// </summary>
    public void Show(BridgeRunReport report)
    {
        HeadlineText.Text = report.Failed == 0
            ? $"Applied {report.Succeeded} ops"
            : $"Applied {report.Succeeded} ops, {report.Failed} failed";
        SummaryText.Text = $"Skipped: {report.Skipped} · cleanup ran: {(report.CleanupRan ? "yes" : "no")}";
        if (report.ErrorMessages.Count > 0)
        {
            ErrorsList.ItemsSource = report.ErrorMessages;
            ErrorsScroller.Visibility = Visibility.Visible;
        }
        else
        {
            ErrorsScroller.Visibility = Visibility.Collapsed;
        }
        LastJournalPath = report.JournalPath;
        UndoButton.IsEnabled = !string.IsNullOrEmpty(LastJournalPath);
        Visibility = Visibility.Visible;
    }

    public void Clear()
    {
        Visibility = Visibility.Collapsed;
        LastJournalPath = null;
        UndoButton.IsEnabled = false;
    }

    private void OnUndoClick(object sender, RoutedEventArgs e)
    {
        if (!string.IsNullOrEmpty(LastJournalPath))
        {
            UndoRequested?.Invoke(this, LastJournalPath);
        }
    }
}
