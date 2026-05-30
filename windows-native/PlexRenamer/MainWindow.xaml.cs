using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Input;
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
    private IReadOnlyList<ResolvedRow> _currentRows = Array.Empty<ResolvedRow>();
    private IReadOnlyList<ResolvedGroup> _currentGroups = Array.Empty<ResolvedGroup>();

    public MainWindow()
    {
        InitializeComponent();
        _currentSettings = _settingsStore.Load();
        StatusFooterControl.SetLibraryRoots(_currentSettings.MoviesRoot, _currentSettings.TvRoot);
        StatusFooterControl.SetEngineState(EngineState.Stopped);
        WireEvents();
        ApplyEngineState();
        // Keyboard shortcuts: Ctrl+, opens Settings, Ctrl+P runs Preview,
        // Ctrl+Enter triggers Apply. Wired here rather than in XAML
        // KeyBinding declarations so they can invoke the existing async
        // handlers directly.
        InputBindings.Add(new KeyBinding(new RelayCommand(_ => OnSettingsClicked(this, EventArgs.Empty)), Key.OemComma, ModifierKeys.Control));
        InputBindings.Add(new KeyBinding(new RelayCommand(_ => OnPreviewClicked(this, EventArgs.Empty)), Key.P, ModifierKeys.Control));
        InputBindings.Add(new KeyBinding(new RelayCommand(_ => OnApplyClicked(this, EventArgs.Empty)), Key.Enter, ModifierKeys.Control));

        Loaded += (_, _) => _ = MaybePromptForTmdbKeyAsync();
    }

    private void WireEvents()
    {
        DropZoneControl.PathsDropped += OnPathsDropped;
        ActionBarControl.PreviewClicked += OnPreviewClicked;
        ActionBarControl.ApplyClicked += OnApplyClicked;
        SourcePanelControl.RowActivated += OnSourceRowActivated;
        SourcePanelControl.GroupAnchorRequested += OnGroupAnchorRequested;
        SourcePanelControl.SearchTmdbRequested += OnSearchTmdbRequested;
        SourcePanelControl.SetImdbRequested += OnSetImdbRequested;
        SourcePanelControl.OverrideRequested += OnOverrideRequested;
        SourcePanelControl.ToggleSkipRequested += OnToggleSkipRequested;
        StatusFooterControl.OpenSettingsRequested += (_, _) => OnSettingsClicked(this, EventArgs.Empty);
        RunReportControl.UndoRequested += OnUndoRequested;
        if (App.EngineClient is not null)
        {
            App.EngineClient.UnexpectedExit += OnEngineUnexpectedExit;
        }
    }

    private async void OnApplyClicked(object? sender, EventArgs e)
    {
        if (_currentRows.Count == 0)
        {
            return;
        }
        try
        {
            await EnsureEngineStartedAsync();
            // 1. build_plan to detect collisions before any FS touch.
            var planResult = await App.EngineClient.BuildPlanAsync(
                _currentRows, inputRoot: null, applyEditions: false, _currentSettings);
            var plan = planResult.Plan;

            // 2. If there are collisions, surface CollisionReviewDialog.
            if (plan.Collisions.Count > 0)
            {
                var collisionDialog = new CollisionReviewDialog(plan.Collisions) { Owner = this };
                if (collisionDialog.ShowDialog() != true)
                {
                    return;
                }
                var rowsAfterSkips = _currentRows;
                foreach (var srcPath in collisionDialog.SkippedSourcePaths)
                {
                    var matching = FindRowByPath(rowsAfterSkips, srcPath);
                    if (matching == null) continue;
                    var editResult = await App.EngineClient.EditRowAsync(
                        rowsAfterSkips, matching.RowId,
                        new EditRowOverrides { Skip = true }, _currentSettings);
                    rowsAfterSkips = editResult.Rows;
                }
                _currentRows = rowsAfterSkips;
                SourcePanelControl.LoadRows(_currentRows, _currentGroups);
                planResult = await App.EngineClient.BuildPlanAsync(
                    _currentRows, inputRoot: null, applyEditions: false, _currentSettings);
                plan = planResult.Plan;
            }

            // 3. Cleanup-confirm modal if settings.CleanupEnabled.
            var cleanupRequested = _currentSettings.CleanupEnabled;
            if (cleanupRequested)
            {
                var paths = new List<string>();
                foreach (var op in plan.Ops) paths.Add(op.Source);
                foreach (var op in plan.Ops)
                {
                    foreach (var sidecar in op.Sidecars)
                    {
                        if (sidecar.Count > 0) paths.Add(sidecar[0]);
                    }
                }
                var cleanupModal = new CleanupConfirmModal(paths) { Owner = this };
                if (cleanupModal.ShowDialog() != true)
                {
                    return;
                }
            }

            // 4. apply_plan (streaming). Per-op events drive ApplyProgress.
            ApplyProgressControl.Begin(plan.Ops.Count);
            RunReportInfoBar.IsOpen = false;
            RunReportControl.Visibility = System.Windows.Visibility.Collapsed;
            StatusFooterControl.SetEngineState(EngineState.Busy);
            Bridge.RunReport? finalReport = null;
            try
            {
                await foreach (var ev in App.EngineClient.ApplyPlanAsync(
                    plan, cleanupRequested, verifyHash: false, _currentSettings))
                {
                    if (ev.EventKind == "done" && ev.Result != null)
                    {
                        finalReport = ev.Result;
                    }
                    else
                    {
                        ApplyProgressControl.UpdateForEvent(ev);
                    }
                }
            }
            finally
            {
                ApplyProgressControl.Hide();
                StatusFooterControl.SetEngineState(_engineStarted ? EngineState.Ready : EngineState.Disconnected);
            }

            if (finalReport != null)
            {
                ShowRunReport(finalReport);
            }
        }
        catch (BridgeException ex)
        {
            System.Windows.MessageBox.Show(
                $"Apply failed:\n\n{ex.Message}",
                "Apply error",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Error);
        }
    }

    private void ShowRunReport(Bridge.RunReport report)
    {
        // Headline goes in the InfoBar so the user sees an at-a-glance
        // pass/fail summary; the legacy RunReport widget carries the
        // Undo button + error list when there's detail worth showing.
        if (report.Failed == 0)
        {
            RunReportInfoBar.Severity = InfoBarSeverity.Success;
            RunReportInfoBar.Title = "Apply complete";
            RunReportInfoBar.Message =
                $"Copied {report.Succeeded} file(s) · skipped {report.Skipped} · cleanup ran: {(report.CleanupRan ? "yes" : "no")}.";
        }
        else
        {
            RunReportInfoBar.Severity = InfoBarSeverity.Warning;
            RunReportInfoBar.Title = $"Apply finished with {report.Failed} error(s)";
            RunReportInfoBar.Message =
                $"Copied {report.Succeeded} file(s) · {report.Failed} failed · skipped {report.Skipped}.";
        }
        RunReportInfoBar.IsOpen = true;
        // Show the detail surface (Undo button + error list) only when
        // there's something to act on: errors to read or an undo
        // available.
        if (report.Failed > 0 || !string.IsNullOrEmpty(report.JournalPath))
        {
            RunReportControl.Show(report);
        }
        else
        {
            RunReportControl.Clear();
        }
    }

    private async void OnUndoRequested(object? sender, string journalPath)
    {
        try
        {
            await EnsureEngineStartedAsync();
            var report = await App.EngineClient.UndoBatchAsync(journalPath);
            var msg = $"Reverted: {report.Reverted}\n"
                + $"Moved to review: {report.MovedToReview}\n"
                + (report.ReviewDir != null ? $"Review dir: {report.ReviewDir}\n" : "")
                + (report.SourcesRecoverable
                    ? "Sources recoverable."
                    : "Sources NOT recoverable — cleanup ran on this batch.");
            System.Windows.MessageBox.Show(
                msg,
                "Undo complete",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Information);
            RunReportControl.Clear();
            RunReportInfoBar.IsOpen = false;
        }
        catch (BridgeException ex)
        {
            System.Windows.MessageBox.Show(
                $"Undo failed:\n\n{ex.Message}",
                "Undo error",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Error);
        }
    }

    private static ResolvedRow? FindRowByPath(IReadOnlyList<ResolvedRow> rows, string sourcePath)
    {
        foreach (var r in rows)
        {
            if (r.Parsed.SourcePath == sourcePath)
            {
                return r;
            }
        }
        return null;
    }

    private async Task MaybePromptForTmdbKeyAsync()
    {
        if (!string.IsNullOrEmpty(_currentSettings.TmdbApiKey))
        {
            return;
        }
        var prompt = new TmdbKeyPrompt { Owner = this };
        if (prompt.ShowDialog() == true && !string.IsNullOrEmpty(prompt.EnteredKey))
        {
            try
            {
                await EnsureEngineStartedAsync();
                var saved = await App.EngineClient.SaveSettingsAsync(_currentSettings with
                {
                    TmdbApiKey = prompt.EnteredKey,
                });
                _currentSettings = saved;
                StatusFooterControl.SetLibraryRoots(saved.MoviesRoot, saved.TvRoot);
            }
            catch (BridgeException ex)
            {
                System.Windows.MessageBox.Show(
                    $"Could not save TMDB key:\n\n{ex.Message}",
                    "TMDB key",
                    System.Windows.MessageBoxButton.OK,
                    System.Windows.MessageBoxImage.Warning);
            }
        }
    }

    private async void OnSourceRowActivated(object? sender, ResolvedRow row)
    {
        await EnsureEngineStartedAsync();
        var dialog = new EditPane(App.EngineClient, _currentSettings, row, _currentRows)
        {
            Owner = this,
        };
        if (dialog.ShowDialog() == true && dialog.UpdatedRows is { } updated)
        {
            _currentRows = updated;
            SourcePanelControl.LoadRows(updated, _currentGroups);
            TargetPanelControl.LoadFrom(new ParseResolveResult
            {
                Rows = updated,
                Groups = _currentGroups,
                InputRoot = string.Empty,
            });
        }
    }

    private async void OnGroupAnchorRequested(object? sender, ResolvedGroup group)
    {
        await EnsureEngineStartedAsync();
        var picker = new ShowAnchorPicker(App.EngineClient, _currentSettings, group.Label)
        {
            Owner = this,
        };
        if (picker.ShowDialog() == true && picker.PickedCandidate is { } candidate)
        {
            try
            {
                var result = await App.EngineClient.SelectAnchorAsync(
                    _currentRows, group.GroupKey, candidate, _currentSettings);
                _currentRows = result.Rows;
                SourcePanelControl.LoadRows(_currentRows, _currentGroups);
                TargetPanelControl.LoadFrom(new ParseResolveResult
                {
                    Rows = _currentRows,
                    Groups = _currentGroups,
                    InputRoot = string.Empty,
                });
            }
            catch (BridgeException ex)
            {
                System.Windows.MessageBox.Show(
                    $"Could not apply anchor:\n\n{ex.Message}",
                    "Anchor selection",
                    System.Windows.MessageBoxButton.OK,
                    System.Windows.MessageBoxImage.Warning);
            }
        }
    }

    private async void OnSearchTmdbRequested(object? sender, ResolvedRow row)
    {
        // Reuse EditPane for the full search flow — slimmer single-field
        // dialogs are an option, but the EditPane already wires TMDB
        // search end-to-end and surfaces the candidate list inline.
        await OnSourceRowActivatedAsync(row);
    }

    private async void OnSetImdbRequested(object? sender, ResolvedRow row)
    {
        await EnsureEngineStartedAsync();
        var dialog = new SingleValuePrompt(
            title: "Set IMDb ID",
            description: $"Look up '{row.Parsed.RawFilename}' by IMDb ID.",
            placeholder: "tt0000000",
            initialValue: row.ImdbIdOverride ?? string.Empty)
        { Owner = this };
        if (dialog.ShowDialog() != true || string.IsNullOrWhiteSpace(dialog.EnteredValue))
        {
            return;
        }
        var imdbId = dialog.EnteredValue.Trim();
        try
        {
            var lookup = await App.EngineClient.FindByImdbAsync(imdbId, row, _currentSettings);
            var editResult = await App.EngineClient.EditRowAsync(
                _currentRows, row.RowId,
                new EditRowOverrides
                {
                    ImdbIdOverride = imdbId,
                    Candidate = lookup.Candidate,
                },
                _currentSettings);
            _currentRows = editResult.Rows;
            SourcePanelControl.LoadRows(_currentRows, _currentGroups);
            TargetPanelControl.LoadFrom(new ParseResolveResult
            {
                Rows = _currentRows,
                Groups = _currentGroups,
                InputRoot = string.Empty,
            });
        }
        catch (BridgeException ex)
        {
            System.Windows.MessageBox.Show(
                $"IMDb lookup failed:\n\n{ex.Message}",
                "IMDb lookup",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Warning);
        }
    }

    private async void OnOverrideRequested(object? sender, RowOverrideRequest req)
    {
        await EnsureEngineStartedAsync();
        var (title, placeholder) = req.Kind switch
        {
            "title" => ("Override title", "e.g. The Matrix"),
            "year" => ("Override year", "e.g. 1999"),
            "season" => ("Override season", "e.g. 2"),
            "episode" => ("Override episode", "e.g. 5"),
            "edition" => ("Override edition", "e.g. Director's Cut"),
            _ => ("Override metadata", string.Empty),
        };
        var rowCount = req.Rows.Count;
        var description = rowCount == 1
            ? $"Apply to '{req.Rows[0].Parsed.RawFilename}'."
            : $"Apply to {rowCount} selected rows.";
        var dialog = new SingleValuePrompt(title, description, placeholder, string.Empty) { Owner = this };
        if (dialog.ShowDialog() != true)
        {
            return;
        }
        var value = dialog.EnteredValue?.Trim() ?? string.Empty;
        try
        {
            var workingRows = _currentRows;
            foreach (var row in req.Rows)
            {
                var overrides = new EditRowOverrides();
                switch (req.Kind)
                {
                    case "title":
                        overrides = new EditRowOverrides { ManualTitle = string.IsNullOrEmpty(value) ? null : value };
                        break;
                    case "year":
                        overrides = new EditRowOverrides { ManualYear = int.TryParse(value, out var y) ? y : (int?)null };
                        break;
                    case "season":
                        overrides = new EditRowOverrides { ManualSeason = int.TryParse(value, out var s) ? s : (int?)null };
                        break;
                    case "episode":
                        overrides = new EditRowOverrides { ManualEpisode = int.TryParse(value, out var ep) ? ep : (int?)null };
                        break;
                    case "edition":
                        overrides = new EditRowOverrides { ManualEdition = string.IsNullOrEmpty(value) ? null : value };
                        break;
                }
                var result = await App.EngineClient.EditRowAsync(workingRows, row.RowId, overrides, _currentSettings);
                workingRows = result.Rows;
            }
            _currentRows = workingRows;
            SourcePanelControl.LoadRows(_currentRows, _currentGroups);
            TargetPanelControl.LoadFrom(new ParseResolveResult
            {
                Rows = _currentRows,
                Groups = _currentGroups,
                InputRoot = string.Empty,
            });
        }
        catch (BridgeException ex)
        {
            System.Windows.MessageBox.Show(
                $"Could not apply override:\n\n{ex.Message}",
                "Override metadata",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Warning);
        }
    }

    private async void OnToggleSkipRequested(object? sender, IReadOnlyList<ResolvedRow> rows)
    {
        if (rows.Count == 0)
        {
            return;
        }
        await EnsureEngineStartedAsync();
        // Toggle on the majority state: if every row is skipped, unskip
        // all; otherwise skip all. Matches the IsCheckable menu item's
        // tristate-ish behavior.
        var nextSkip = !rows.All(r => r.Skip);
        try
        {
            var workingRows = _currentRows;
            foreach (var row in rows)
            {
                var result = await App.EngineClient.EditRowAsync(
                    workingRows, row.RowId,
                    new EditRowOverrides { Skip = nextSkip },
                    _currentSettings);
                workingRows = result.Rows;
            }
            _currentRows = workingRows;
            SourcePanelControl.LoadRows(_currentRows, _currentGroups);
            TargetPanelControl.LoadFrom(new ParseResolveResult
            {
                Rows = _currentRows,
                Groups = _currentGroups,
                InputRoot = string.Empty,
            });
        }
        catch (BridgeException ex)
        {
            System.Windows.MessageBox.Show(
                $"Could not update skip state:\n\n{ex.Message}",
                "Skip row",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Warning);
        }
    }

    private void OnEngineUnexpectedExit(object? sender, EngineExitedEventArgs e)
    {
        _engineStarted = false;
        Dispatcher.BeginInvoke(() =>
        {
            StatusFooterControl.SetEngineState(EngineState.Disconnected);
            ApplyEngineState();
        });
    }

    internal async Task RestartEngineAsync()
    {
        try
        {
            await App.EngineClient.RestartAsync();
            _engineStarted = true;
            StatusFooterControl.SetEngineState(EngineState.Ready);
        }
        catch (Exception ex)
        {
            System.Windows.MessageBox.Show(
                $"Could not restart the engine sidecar.\n\n{ex.Message}",
                "Engine restart failed",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Error);
        }
        ApplyEngineState();
    }

    private async void OnPathsDropped(object? sender, IReadOnlyList<string> paths)
    {
        await EnsureEngineStartedAsync();
        await RunParseAndResolveAsync(paths);
    }

    private async void OnPreviewClicked(object? sender, EventArgs e)
    {
        var paths = SourcePanelControl.CurrentSourcePaths;
        if (paths.Count == 0)
        {
            return;
        }
        await EnsureEngineStartedAsync();
        await RunParseAndResolveAsync(paths);
    }

    private async void OnSettingsClicked(object? sender, EventArgs e)
    {
        var dialog = new SettingsDialog(_currentSettings) { Owner = this };
        if (dialog.ShowDialog() != true)
        {
            return;
        }
        try
        {
            await EnsureEngineStartedAsync();
            _currentSettings = await App.EngineClient.SaveSettingsAsync(dialog.Result);
            StatusFooterControl.SetLibraryRoots(_currentSettings.MoviesRoot, _currentSettings.TvRoot);
        }
        catch (BridgeException ex)
        {
            _currentSettings = dialog.Result;
            _settingsStore.Save(_currentSettings);
            StatusFooterControl.SetLibraryRoots(_currentSettings.MoviesRoot, _currentSettings.TvRoot);
            System.Windows.MessageBox.Show(
                $"Settings saved locally; the engine sidecar reported an error " +
                $"that may need restart to clear:\n\n{ex.Message}",
                "Settings save (daemon)",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Warning);
        }
        ApplyEngineState();
    }

    private void OnSettingsTitleBarClick(object sender, RoutedEventArgs e)
        => OnSettingsClicked(this, EventArgs.Empty);

    private async Task EnsureEngineStartedAsync()
    {
        if (_engineStarted)
        {
            return;
        }
        try
        {
            StatusFooterControl.SetEngineState(EngineState.Busy);
            await App.EngineClient.StartAsync();
            _engineStarted = true;
            StatusFooterControl.SetEngineState(EngineState.Ready);
            ApplyEngineState();
        }
        catch (Exception ex)
        {
            StatusFooterControl.SetEngineState(EngineState.Disconnected);
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
            StatusFooterControl.SetEngineState(EngineState.Busy);
            var result = await App.EngineClient.ParseAndResolveAsync(paths, _currentSettings);
            _currentRows = result.Rows;
            _currentGroups = result.Groups;
            SourcePanelControl.LoadFrom(result);
            TargetPanelControl.LoadFrom(result);
            DropZoneControl.SetLoadedState(result.Rows.Count);
            ApplyEngineState();
        }
        catch (BridgeException ex)
        {
            System.Windows.MessageBox.Show(
                $"The engine returned an error:\n\n{ex.Message}",
                "Parse / resolve failed",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Error);
        }
        finally
        {
            StatusFooterControl.SetEngineState(_engineStarted ? EngineState.Ready : EngineState.Disconnected);
        }
    }

    private async Task OnSourceRowActivatedAsync(ResolvedRow row)
    {
        await EnsureEngineStartedAsync();
        var dialog = new EditPane(App.EngineClient, _currentSettings, row, _currentRows)
        {
            Owner = this,
        };
        if (dialog.ShowDialog() == true && dialog.UpdatedRows is { } updated)
        {
            _currentRows = updated;
            SourcePanelControl.LoadRows(updated, _currentGroups);
            TargetPanelControl.LoadFrom(new ParseResolveResult
            {
                Rows = updated,
                Groups = _currentGroups,
                InputRoot = string.Empty,
            });
        }
    }

    private void ApplyEngineState()
    {
        var hasFiles = SourcePanelControl.CurrentSourcePaths.Count > 0;
        var rootsSet = !string.IsNullOrEmpty(_currentSettings.MoviesRoot)
            || !string.IsNullOrEmpty(_currentSettings.TvRoot);
        ActionBarControl.IsPreviewEnabled = hasFiles && _engineStarted;
        var canApply = hasFiles && _engineStarted && rootsSet
            && _currentRows.Any(r => !r.Skip);
        ActionBarControl.IsApplyEnabled = canApply;
        ActionBarControl.ApplyTooltip = canApply
            ? "Copy resolved files to their Plex destinations (Ctrl+Enter)."
            : (!hasFiles ? "Drop files first."
               : !rootsSet ? "Set library roots in Settings."
               : !_engineStarted ? "Engine sidecar not started."
               : "All rows are marked Skip.");
    }
}

internal sealed class RelayCommand : ICommand
{
    private readonly Action<object?> _execute;
    public RelayCommand(Action<object?> execute) { _execute = execute; }
    public bool CanExecute(object? parameter) => true;
    public void Execute(object? parameter) => _execute(parameter);
    public event EventHandler? CanExecuteChanged;
}
