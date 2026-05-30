using System;
using System.IO;
using System.Windows;
using System.Windows.Controls;

namespace PlexRenamer.Views;

/// <summary>
/// Persistent one-line footer below the action bar. Shows the configured
/// library roots (read-only — clicking opens Settings) on the left, and
/// engine sidecar status + an indeterminate spinner on the right while
/// a long-running engine call is in flight.
/// </summary>
public partial class StatusFooter : UserControl
{
    public event EventHandler? OpenSettingsRequested;

    public StatusFooter()
    {
        InitializeComponent();
    }

    public void SetLibraryRoots(string? moviesRoot, string? tvRoot)
    {
        var hasMovies = !string.IsNullOrWhiteSpace(moviesRoot);
        var hasTv = !string.IsNullOrWhiteSpace(tvRoot);
        if (!hasMovies && !hasTv)
        {
            LibraryRootsText.Text = "Set library roots in Settings →";
            LibraryRootsButton.ToolTip = "Movies and TV library roots are not set.";
            return;
        }
        var moviesLabel = hasMovies ? CompactPath(moviesRoot!) : "(not set)";
        var tvLabel = hasTv ? CompactPath(tvRoot!) : "(not set)";
        LibraryRootsText.Text = $"Movies → {moviesLabel}  ·  TV → {tvLabel}";
        LibraryRootsButton.ToolTip = $"Movies: {moviesRoot ?? "(not set)"}\nTV: {tvRoot ?? "(not set)"}";
    }

    public void SetEngineState(EngineState state)
    {
        switch (state)
        {
            case EngineState.Stopped:
                EngineStatusText.Text = "Engine: not started";
                EngineSpinner.Visibility = Visibility.Collapsed;
                break;
            case EngineState.Ready:
                EngineStatusText.Text = "Engine: ready";
                EngineSpinner.Visibility = Visibility.Collapsed;
                break;
            case EngineState.Busy:
                EngineStatusText.Text = "Engine: working…";
                EngineSpinner.Visibility = Visibility.Visible;
                break;
            case EngineState.Disconnected:
                EngineStatusText.Text = "Engine: disconnected";
                EngineSpinner.Visibility = Visibility.Collapsed;
                break;
        }
    }

    private void OnLibraryRootsClick(object sender, RoutedEventArgs e)
    {
        OpenSettingsRequested?.Invoke(this, EventArgs.Empty);
    }

    private static string CompactPath(string path)
    {
        // Show the parent directory + leaf only so long Windows paths
        // ("D:\Plex\Media Library\Television Shows") still fit in the
        // footer without truncation at the character level. Full path
        // is on the tooltip.
        try
        {
            var leaf = Path.GetFileName(path.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
            var parent = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(parent) && !string.IsNullOrEmpty(leaf))
            {
                var parentLeaf = Path.GetFileName(parent.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
                if (!string.IsNullOrEmpty(parentLeaf))
                {
                    return $"…\\{parentLeaf}\\{leaf}";
                }
                return $"…\\{leaf}";
            }
            return path;
        }
        catch
        {
            return path;
        }
    }
}

public enum EngineState
{
    Stopped,
    Ready,
    Busy,
    Disconnected,
}
