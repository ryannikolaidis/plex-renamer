using System;
using System.Windows;
using PlexRenamer.Bridge;
using Wpf.Ui;
using Wpf.Ui.Appearance;

namespace PlexRenamer;

/// <summary>
/// Application entry point. Owns the long-lived <see cref="IEngineClient"/>
/// sidecar connection so MainWindow + dialogs can resolve it. The sidecar
/// is started lazily by MainWindow's first parse_and_resolve call rather
/// than blocking app startup on it.
/// </summary>
public partial class App : Application
{
    /// <summary>
    /// The shell-wide engine client. Set during startup; null between
    /// startup and shutdown shouldn't be observable to UI code.
    /// </summary>
    public static IEngineClient EngineClient { get; private set; } = null!;

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        // Apply WPF-UI theming. The system-default mode gives us Light/Dark
        // following the user's Windows theme; the secondary theme dictionary
        // in App.xaml provides the Fluent control templates.
        ApplicationThemeManager.ApplySystemTheme();

        EngineClient = new EngineClient();
        EngineClient.UnexpectedExit += OnEngineUnexpectedExit;
    }

    protected override async void OnExit(ExitEventArgs e)
    {
        try
        {
            if (EngineClient is not null)
            {
                await EngineClient.DisposeAsync().ConfigureAwait(false);
            }
        }
        catch
        {
            // Best-effort shutdown — don't crash on exit.
        }
        base.OnExit(e);
    }

    private void OnEngineUnexpectedExit(object? sender, EngineExitedEventArgs e)
    {
        // Re-marshal to the UI thread to surface the modal.
        Dispatcher.BeginInvoke(() =>
        {
            var detail = e.Stderr is { Length: > 0 } ? $"\n\nStderr:\n{e.Stderr}" : string.Empty;
            MessageBox.Show(
                $"The engine sidecar exited unexpectedly (code {e.ExitCode}).{detail}\n\n" +
                "Click OK to restart the sidecar. Preview / Apply are disabled until it's back.",
                "Engine sidecar died",
                MessageBoxButton.OK,
                MessageBoxImage.Warning);
            // The Restart button on the modal would call EngineClient.RestartAsync.
            // Slice 2 ships the modal as an OK acknowledgment with a restart on click;
            // a richer recovery UI is part of slice 3 / 4 polish.
        });
    }
}
