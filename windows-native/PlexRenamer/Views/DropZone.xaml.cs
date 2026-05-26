using System;
using System.Collections.Generic;
using System.Windows;
using System.Windows.Controls;

namespace PlexRenamer.Views;

public partial class DropZone : UserControl
{
    public event EventHandler<IReadOnlyList<string>>? PathsDropped;

    public DropZone()
    {
        InitializeComponent();
        Drop += OnDrop;
        DragEnter += OnDragEnter;
        DragLeave += OnDragLeave;
    }

    private void OnDragEnter(object sender, DragEventArgs e)
    {
        if (e.Data.GetDataPresent(DataFormats.FileDrop))
        {
            e.Effects = DragDropEffects.Copy;
            HeadlineText.Text = "Release to drop";
        }
        else
        {
            e.Effects = DragDropEffects.None;
        }
        e.Handled = true;
    }

    private void OnDragLeave(object sender, DragEventArgs e)
    {
        HeadlineText.Text = "Drop files or folders here";
    }

    private void OnDrop(object sender, DragEventArgs e)
    {
        HeadlineText.Text = "Drop files or folders here";
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
}
