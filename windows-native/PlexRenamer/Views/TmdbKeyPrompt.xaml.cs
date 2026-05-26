using System.Windows;
using Wpf.Ui.Controls;

namespace PlexRenamer.Views;

public partial class TmdbKeyPrompt : FluentWindow
{
    public string? EnteredKey { get; private set; }

    public TmdbKeyPrompt()
    {
        InitializeComponent();
    }

    private void OnSaveClick(object sender, RoutedEventArgs e)
    {
        var key = KeyBox.Text?.Trim();
        if (string.IsNullOrEmpty(key))
        {
            EnteredKey = null;
        }
        else
        {
            EnteredKey = key;
        }
        DialogResult = !string.IsNullOrEmpty(EnteredKey);
    }

    private void OnSkipClick(object sender, RoutedEventArgs e)
    {
        EnteredKey = null;
        DialogResult = false;
    }
}
