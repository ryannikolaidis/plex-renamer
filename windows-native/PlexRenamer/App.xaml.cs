using System;
using System.Reflection;
using System.Runtime.InteropServices;
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

    // OutputType=WinExe detaches stdout from the parent terminal. Re-attach
    // to the launching console so --version prints visibly when the binary
    // is invoked from PowerShell / cmd; without this the version string is
    // discarded and the post-NSIS smoke can't observe the exit cleanly.
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool AttachConsole(int dwProcessId);

    private const int ATTACH_PARENT_PROCESS = -1;

    protected override void OnStartup(StartupEventArgs e)
    {
        if (e.Args.Length > 0 && (e.Args[0] == "--version" || e.Args[0] == "-v"))
        {
            AttachConsole(ATTACH_PARENT_PROCESS);
            var version =
                Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "unknown";
            Console.WriteLine($"plex-renamer {version}");
            Shutdown(0);
            return;
        }

        base.OnStartup(e);

        // Apply WPF-UI theming. The system-default mode gives us Light/Dark
        // following the user's Windows theme; the secondary theme dictionary
        // in App.xaml provides the Fluent control templates.
        ApplicationThemeManager.ApplySystemTheme();

        EngineClient = new EngineClient();
        EngineClient.UnexpectedExit += OnEngineUnexpectedExit;
    }

    protected override void OnExit(ExitEventArgs e)
    {
        // WPF's OnExit is sync-only; `async void` would let the framework
        // tear down the process before DisposeAsync finishes, leaving the
        // sidecar orphaned. Block synchronously with a 5s timeout so the
        // sidecar gets a clean shutdown signal but a hung dispose doesn't
        // hold up app exit forever.
        try
        {
            if (EngineClient is not null)
            {
                var disposeTask = EngineClient.DisposeAsync().AsTask();
                disposeTask.Wait(TimeSpan.FromSeconds(5));
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
        // Re-marshal to the UI thread to surface the modal, then trigger the
        // restart on OK. The MainWindow's own UnexpectedExit handler already
        // resets _engineStarted and disables Preview/Apply; this modal owns
        // the actual restart trigger so the user can decide when to retry.
        Dispatcher.BeginInvoke(async () =>
        {
            var detail = e.Stderr is { Length: > 0 } ? $"\n\nStderr:\n{e.Stderr}" : string.Empty;
            var result = System.Windows.MessageBox.Show(
                $"The engine sidecar exited unexpectedly (code {e.ExitCode}).{detail}\n\n" +
                "Click OK to restart the sidecar. Cancel keeps Preview / Apply disabled.",
                "Engine sidecar died",
                System.Windows.MessageBoxButton.OKCancel,
                System.Windows.MessageBoxImage.Warning);
            if (result == System.Windows.MessageBoxResult.OK
                && Current.MainWindow is MainWindow mainWindow)
            {
                await mainWindow.RestartEngineAsync().ConfigureAwait(false);
            }
        });
    }
}
