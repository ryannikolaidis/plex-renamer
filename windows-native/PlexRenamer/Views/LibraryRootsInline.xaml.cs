using System;
using System.Windows.Controls;
using Microsoft.Win32;

namespace PlexRenamer.Views;

public sealed class LibraryRootsChangedEventArgs : EventArgs
{
    public required string? MoviesRoot { get; init; }
    public required string? TvRoot { get; init; }
}

public partial class LibraryRootsInline : UserControl
{
    public event EventHandler<LibraryRootsChangedEventArgs>? RootsChanged;

    private string? _moviesRoot;
    private string? _tvRoot;

    public LibraryRootsInline()
    {
        InitializeComponent();
    }

    public void SetRoots(string? moviesRoot, string? tvRoot)
    {
        _moviesRoot = moviesRoot;
        _tvRoot = tvRoot;
        MoviesRootText.Text = string.IsNullOrEmpty(moviesRoot) ? "(not set)" : moviesRoot;
        TvRootText.Text = string.IsNullOrEmpty(tvRoot) ? "(not set)" : tvRoot;
    }

    private void OnChangeMoviesClick(object sender, System.Windows.RoutedEventArgs e)
    {
        var dialog = new OpenFolderDialog { Title = "Movies library root" };
        if (dialog.ShowDialog() == true)
        {
            _moviesRoot = dialog.FolderName;
            MoviesRootText.Text = _moviesRoot;
            RootsChanged?.Invoke(this, new LibraryRootsChangedEventArgs
            {
                MoviesRoot = _moviesRoot,
                TvRoot = _tvRoot,
            });
        }
    }

    private void OnChangeTvClick(object sender, System.Windows.RoutedEventArgs e)
    {
        var dialog = new OpenFolderDialog { Title = "TV library root" };
        if (dialog.ShowDialog() == true)
        {
            _tvRoot = dialog.FolderName;
            TvRootText.Text = _tvRoot;
            RootsChanged?.Invoke(this, new LibraryRootsChangedEventArgs
            {
                MoviesRoot = _moviesRoot,
                TvRoot = _tvRoot,
            });
        }
    }
}
