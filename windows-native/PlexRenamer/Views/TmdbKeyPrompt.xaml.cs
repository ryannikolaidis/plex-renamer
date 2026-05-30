using System.Diagnostics;
using System.Windows;
using System.Windows.Navigation;
using Wpf.Ui.Controls;

namespace PlexRenamer.Views;

public partial class TmdbKeyPrompt : FluentWindow
{
    public string? EnteredKey { get; private set; }

    public TmdbKeyPrompt()
    {
        InitializeComponent();
    }

    private void OnHyperlinkRequestNavigate(object sender, RequestNavigateEventArgs e)
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = e.Uri.AbsoluteUri,
                UseShellExecute = true,
            });
        }
        catch
        {
            // No browser configured; silently fail.
        }
        e.Handled = true;
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
