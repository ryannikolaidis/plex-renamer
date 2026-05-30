using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using Microsoft.Win32;

namespace PlexRenamer.Views;

public partial class DropZone : UserControl
{
    public event EventHandler<IReadOnlyList<string>>? PathsDropped;

    private static readonly Color HoverBorderColor = Color.FromRgb(0x00, 0x78, 0xD4);

    public DropZone()
    {
        InitializeComponent();
        Drop += OnDrop;
        DragEnter += OnDragEnter;
        DragLeave += OnDragLeave;
        DragOver += OnDragOver;
    }

    /// <summary>
    /// Collapse the drop-zone chrome to a thin status bar once content
    /// has been loaded. The user can still drop more files or hit Browse.
    /// </summary>
    public void SetLoadedState(int fileCount)
    {
        if (fileCount == 0)
        {
            HeadlineText.Text = "Drop files or folders here";
            HelperText.Text = "Movies and TV folders both work · or click Browse →";
            HeadlineText.FontSize = 16;
        }
        else
        {
            HeadlineText.Text = $"{fileCount} file(s) loaded";
            HelperText.Text = "Drop more to append, or right-click a row to act on it.";
            HeadlineText.FontSize = 13;
        }
    }

    private void OnDragEnter(object sender, DragEventArgs e)
    {
        if (e.Data.GetDataPresent(DataFormats.FileDrop))
        {
            e.Effects = DragDropEffects.Copy;
            HeadlineText.Text = "Release to drop";
            DropBorder.BorderBrush = new SolidColorBrush(HoverBorderColor);
        }
        else
        {
            e.Effects = DragDropEffects.None;
        }
        e.Handled = true;
    }

    private void OnDragOver(object sender, DragEventArgs e)
    {
        // Keep the cursor effect aligned with what we set on DragEnter.
        e.Effects = e.Data.GetDataPresent(DataFormats.FileDrop)
            ? DragDropEffects.Copy : DragDropEffects.None;
        e.Handled = true;
    }

    private void OnDragLeave(object sender, DragEventArgs e)
    {
        ResetVisualState();
    }

    private void OnDrop(object sender, DragEventArgs e)
    {
        ResetVisualState();
        if (!e.Data.GetDataPresent(DataFormats.FileDrop))
        {
            return;
        }
        if (e.Data.GetData(DataFormats.FileDrop) is not string[] paths || paths.Length == 0)
        {
            return;
        }
        PathsDropped?.Invoke(this, paths);
    }

    private void OnBrowseClick(object sender, RoutedEventArgs e)
    {
        // We allow multi-file selection via OpenFileDialog. For folder
        // selection the user drags from Explorer; a folder picker UX would
        // require a second button and the eval calls out "Browse…" as a
        // single keyboard-accessible affordance.
        var dialog = new OpenFileDialog
        {
            Multiselect = true,
            Title = "Pick files to load",
        };
        if (dialog.ShowDialog() == true && dialog.FileNames.Length > 0)
        {
            PathsDropped?.Invoke(this, dialog.FileNames);
        }
    }

    private void ResetVisualState()
    {
        // Restore the original "Drop files or folders here" headline only
        // if the zone is in its empty state. After load, SetLoadedState
        // controls the headline; resetting back to the empty-state text
        // here would clobber the post-load count display.
        HeadlineText.Text = HeadlineText.Text == "Release to drop"
            ? "Drop files or folders here"
            : HeadlineText.Text;
        DropBorder.BorderBrush = (System.Windows.Media.Brush)FindResource("ControlStrokeColorDefaultBrush");
    }
}
