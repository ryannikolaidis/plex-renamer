using System.Windows;
using PlexRenamer.Bridge.Schemas;
using Wpf.Ui.Controls;

namespace PlexRenamer.Views;

public partial class SettingsDialog : FluentWindow
{
    public Settings Result { get; private set; }

    public SettingsDialog(Settings initial)
    {
        InitializeComponent();
        Result = initial;
        TmdbKeyBox.Text = initial.TmdbApiKey ?? string.Empty;
        OmdbKeyBox.Text = initial.OmdbApiKey ?? string.Empty;
        MoviesRootBox.Text = initial.MoviesRoot ?? string.Empty;
        TvRootBox.Text = initial.TvRoot ?? string.Empty;
        CleanupCheckbox.IsChecked = initial.CleanupEnabled;
        AutoAcceptCheckbox.IsChecked = initial.AutoAcceptTopHit;
    }

    private void OnSaveClick(object sender, RoutedEventArgs e)
    {
        Result = new Settings
        {
            TmdbApiKey = NullIfEmpty(TmdbKeyBox.Text),
            OmdbApiKey = NullIfEmpty(OmdbKeyBox.Text),
            MoviesRoot = NullIfEmpty(MoviesRootBox.Text),
            TvRoot = NullIfEmpty(TvRootBox.Text),
            CleanupEnabled = CleanupCheckbox.IsChecked == true,
            AutoAcceptTopHit = AutoAcceptCheckbox.IsChecked == true,
        };
        DialogResult = true;
    }

    private void OnCancelClick(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
    }

    private static string? NullIfEmpty(string? s) => string.IsNullOrWhiteSpace(s) ? null : s;
}
