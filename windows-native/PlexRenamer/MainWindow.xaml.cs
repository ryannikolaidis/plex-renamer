using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using System.Windows;
using PlexRenamer.Bridge;
using PlexRenamer.Bridge.Schemas;
using PlexRenamer.Configuration;
using PlexRenamer.Views;
using Wpf.Ui.Controls;

namespace PlexRenamer;

public partial class MainWindow : FluentWindow
{
    private readonly SettingsStore _settingsStore = new();
    private Bridge.Schemas.Settings _currentSettings;
    private bool _engineStarted;

    public MainWindow()
    {
        InitializeComponent();
        _currentSettings = _settingsStore.Load();
        WireEvents();
        ApplyEngineState();
    }

    private void WireEvents()
    {
        DropZoneControl.PathsDropped += OnPathsDropped;
        ActionBarControl.PreviewClicked += OnPreviewClicked;
        ActionBarControl.SettingsClicked += OnSettingsClicked;
        // Apply stays disabled in slice 2; slice 4 wires it.
        ActionBarControl.IsApplyEnabled = false;
        ActionBarControl.ApplyTooltip =
            "Collision review and cleanup confirmation arrive in a later step.";

        if (App.EngineClient is not null)
        {
            App.EngineClient.UnexpectedExit += (_, _) => Dispatcher.BeginInvoke(ApplyEngineState);
        }
    }

    private async void OnPathsDropped(object? sender, IReadOnlyList<string> paths)
    {
        await EnsureEngineStartedAsync();
        await RunParseAndResolveAsync(paths);
    }

    private async void OnPreviewClicked(object? sender, EventArgs e)
    {
        // The Preview button re-runs parse_and_resolve with whatever
        // paths are currently held in the source panel's state. Slice 2
        // ships a basic version; slice 3 adds the manual-edit-then-preview
        // round-trip.
        var paths = SourcePanelControl.CurrentSourcePaths;
        if (paths.Count == 0)
        {
            return;
        }
        await EnsureEngineStartedAsync();
        await RunParseAndResolveAsync(paths);
    }

    private void OnSettingsClicked(object? sender, EventArgs e)
    {
        var dialog = new SettingsDialog(_currentSettings) { Owner = this };
        if (dialog.ShowDialog() == true)
        {
            _currentSettings = dialog.Result;
            _settingsStore.Save(_currentSettings);
        }
    }

    private async Task EnsureEngineStartedAsync()
    {
        if (_engineStarted)
        {
            return;
        }
        try
        {
            await App.EngineClient.StartAsync();
            _engineStarted = true;
            ApplyEngineState();
        }
        catch (Exception ex)
        {
            System.Windows.MessageBox.Show(
                $"Failed to start the engine sidecar.\n\n{ex.Message}\n\nSee " +
                "windows-native/README.md for the dev-mode spawn rule.",
                "Engine startup failed",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Error);
        }
    }

    private async Task RunParseAndResolveAsync(IReadOnlyList<string> paths)
    {
        try
        {
            var result = await App.EngineClient.ParseAndResolveAsync(paths, _currentSettings);
            SourcePanelControl.LoadFrom(result);
            TargetPanelControl.LoadFrom(result);
        }
        catch (BridgeException ex)
        {
            System.Windows.MessageBox.Show(
                $"The engine returned an error:\n\n{ex.Message}",
                "Parse / resolve failed",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Error);
        }
    }

    private void ApplyEngineState()
    {
        // Preview is enabled once paths have been dropped AND the engine
        // is alive. Apply stays disabled in slice 2 regardless of engine
        // state (the safety modals don't exist yet).
        ActionBarControl.IsPreviewEnabled = SourcePanelControl.CurrentSourcePaths.Count > 0
            && _engineStarted;
    }
}
