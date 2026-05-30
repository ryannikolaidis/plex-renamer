using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Input;
using PlexRenamer.Bridge;
using PlexRenamer.Bridge.Schemas;
using Wpf.Ui.Controls;

namespace PlexRenamer.Views;

/// <summary>
/// Per-row edit dialog. Wires the daemon's <c>search_tmdb_free</c>,
/// <c>find_by_imdb</c>, and <c>edit_row</c> RPCs. Save assembles the
/// overrides into an <see cref="EditRowOverrides"/> + (optionally) a
/// picked candidate and calls <c>edit_row</c>; the result rows replace
/// the in-window state.
/// </summary>
public partial class EditPane : FluentWindow
{
    private readonly IEngineClient _engineClient;
    private readonly Settings? _settings;
    private readonly ResolvedRow _initialRow;
    private readonly ObservableCollection<Candidate> _tmdbResults = new();
    private Candidate? _pickedCandidate;

    /// <summary>The row passed in, mutated with the user's edits. Null on cancel.</summary>
    public IReadOnlyList<ResolvedRow>? UpdatedRows { get; private set; }

    public EditPane(
        IEngineClient engineClient,
        Settings? settings,
        ResolvedRow row,
        IReadOnlyList<ResolvedRow> allRows)
    {
        _engineClient = engineClient;
        _settings = settings;
        _initialRow = row;
        AllRows = allRows;
        InitializeComponent();

        SourceFilenameText.Text = row.Parsed.RawFilename;
        SourcePathText.Text = row.Parsed.SourcePath;
        SourcePathText.ToolTip = row.Parsed.SourcePath;
        TmdbQueryBox.Text = row.Parsed.TitleCandidate ?? string.Empty;
        ImdbIdBox.Text = row.ImdbIdOverride ?? string.Empty;
        ManualTitleBox.Text = row.ManualTitle ?? string.Empty;
        ManualYearBox.Text = row.ManualYear?.ToString() ?? string.Empty;
        ManualSeasonBox.Text = row.ManualSeason?.ToString() ?? string.Empty;
        ManualEpisodeBox.Text = row.ManualEpisode?.ToString() ?? string.Empty;
        ManualEditionBox.Text = row.ManualEdition ?? string.Empty;
        SkipCheckbox.IsChecked = row.Skip;
        TmdbResultsListBox.ItemsSource = _tmdbResults;
    }

    private IReadOnlyList<ResolvedRow> AllRows { get; }

    private async void OnTmdbQueryKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter)
        {
            e.Handled = true;
            await RunTmdbSearchAsync();
        }
    }

    private async void OnTmdbSearchClick(object sender, RoutedEventArgs e)
    {
        await RunTmdbSearchAsync();
    }

    private async Task RunTmdbSearchAsync()
    {
        var query = TmdbQueryBox.Text?.Trim() ?? string.Empty;
        if (string.IsNullOrEmpty(query))
        {
            _tmdbResults.Clear();
            return;
        }
        try
        {
            var result = await _engineClient.SearchTmdbFreeAsync(query, "any", _settings);
            _tmdbResults.Clear();
            foreach (var c in result.Candidates)
            {
                _tmdbResults.Add(c);
            }
        }
        catch (BridgeException ex)
        {
            System.Windows.MessageBox.Show(
                $"TMDB search failed:\n\n{ex.Message}",
                "TMDB search",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Warning);
        }
    }

    private void OnTmdbResultSelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        _pickedCandidate = TmdbResultsListBox.SelectedItem as Candidate;
    }

    private async void OnImdbLookupClick(object sender, RoutedEventArgs e)
    {
        var imdbId = ImdbIdBox.Text?.Trim() ?? string.Empty;
        if (string.IsNullOrEmpty(imdbId))
        {
            return;
        }
        try
        {
            var result = await _engineClient.FindByImdbAsync(imdbId, _initialRow, _settings);
            _pickedCandidate = result.Candidate;
            System.Windows.MessageBox.Show(
                $"Resolved to: {result.Candidate.Title} ({result.Candidate.Year}) "
                + $"[anchor: {result.Candidate.AnchorKind}-{result.Candidate.AnchorId}, "
                + $"confidence: {result.Candidate.Confidence:F2}]\n\nSave to apply this candidate.",
                "IMDb resolved",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Information);
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

    private async void OnSaveClick(object sender, RoutedEventArgs e)
    {
        var overrides = new EditRowOverrides
        {
            ManualTitle = NullIfEmpty(ManualTitleBox.Text),
            ManualYear = ParseNullableInt(ManualYearBox.Text),
            ManualSeason = ParseNullableInt(ManualSeasonBox.Text),
            ManualEpisode = ParseNullableInt(ManualEpisodeBox.Text),
            ManualEdition = NullIfEmpty(ManualEditionBox.Text),
            ImdbIdOverride = NullIfEmpty(ImdbIdBox.Text),
            Skip = SkipCheckbox.IsChecked,
            Candidate = _pickedCandidate,
        };
        try
        {
            var result = await _engineClient.EditRowAsync(AllRows, _initialRow.RowId, overrides, _settings);
            UpdatedRows = result.Rows;
            DialogResult = true;
        }
        catch (BridgeException ex)
        {
            System.Windows.MessageBox.Show(
                $"Save failed:\n\n{ex.Message}",
                "Edit row",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Error);
        }
    }

    private void OnCancelClick(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
    }

    private static string? NullIfEmpty(string? s) => string.IsNullOrWhiteSpace(s) ? null : s.Trim();

    private static int? ParseNullableInt(string? s)
        => int.TryParse(s?.Trim(), out var v) ? v : null;
}
