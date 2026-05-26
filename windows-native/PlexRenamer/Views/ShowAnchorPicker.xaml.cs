using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Windows;
using System.Windows.Input;
using System.Windows.Navigation;
using PlexRenamer.Bridge;
using PlexRenamer.Bridge.Schemas;
using Wpf.Ui.Controls;

namespace PlexRenamer.Views;

/// <summary>
/// Modal for picking the TMDB anchor for an unanchored TV group.
/// Drives `iterate_anchor_search` (with cleaned-variant retry) on every
/// query change, and `select_anchor` on Pick. Each candidate exposes a
/// "View on TMDB" / "View on IMDb" hyperlink that opens the canonical
/// record in the system browser.
/// </summary>
public partial class ShowAnchorPicker : FluentWindow
{
    private readonly IEngineClient _engineClient;
    private readonly Settings? _settings;
    private readonly ObservableCollection<CandidateView> _candidates = new();

    /// <summary>The picked candidate, or null if the user cancelled.</summary>
    public Candidate? PickedCandidate { get; private set; }

    public ShowAnchorPicker(IEngineClient engineClient, Settings? settings, string initialQuery)
    {
        _engineClient = engineClient;
        _settings = settings;
        InitializeComponent();
        CandidatesListBox.ItemsSource = _candidates;
        QueryBox.Text = initialQuery;
        // Initial search on open.
        Loaded += (_, _) => _ = RunSearchAsync();
    }

    private async void OnQueryKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter)
        {
            e.Handled = true;
            await RunSearchAsync();
        }
    }

    private async System.Threading.Tasks.Task RunSearchAsync()
    {
        var query = QueryBox.Text?.Trim() ?? string.Empty;
        if (string.IsNullOrEmpty(query))
        {
            _candidates.Clear();
            VariantNote.Visibility = Visibility.Collapsed;
            return;
        }
        try
        {
            var result = await _engineClient.IterateAnchorSearchAsync(query, year: null, _settings);
            _candidates.Clear();
            foreach (var c in result.Candidates)
            {
                _candidates.Add(new CandidateView(c));
            }
            if (result.VariantUsed != null
                && result.VariantOriginal != null
                && !string.Equals(result.VariantUsed, result.VariantOriginal, StringComparison.Ordinal))
            {
                VariantNote.Text =
                    $"No results for \"{result.VariantOriginal}\"; showing results for \"{result.VariantUsed}\" instead.";
                VariantNote.Visibility = Visibility.Visible;
            }
            else
            {
                VariantNote.Visibility = Visibility.Collapsed;
            }
        }
        catch (BridgeException ex)
        {
            System.Windows.MessageBox.Show(
                $"Search failed:\n\n{ex.Message}",
                "TMDB search",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Warning);
        }
    }

    private void OnSelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        PickButton.IsEnabled = CandidatesListBox.SelectedItem is CandidateView;
    }

    private void OnPickClick(object sender, RoutedEventArgs e)
    {
        if (CandidatesListBox.SelectedItem is CandidateView view)
        {
            PickedCandidate = view.Source;
            DialogResult = true;
        }
    }

    private void OnCancelClick(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
    }

    private void OnHyperlinkClick(object sender, RequestNavigateEventArgs e)
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
            // The default browser failed to launch; not worth a modal.
        }
        e.Handled = true;
    }
}

/// <summary>
/// View-model wrapper around a daemon-returned Candidate that exposes
/// the "View on TMDB" / "View on IMDb" hyperlink target as a pre-built
/// Uri. Centralizes the anchor-kind switch so XAML bindings stay
/// declarative.
/// </summary>
public sealed class CandidateView
{
    public Candidate Source { get; }
    public string Title => Source.Title;
    public int? Year => Source.Year;
    public Uri ExternalUrl { get; }
    public string ExternalUrlLabel => Source.AnchorKind == "imdb" ? "View on IMDb" : "View on TMDB";

    public CandidateView(Candidate source)
    {
        Source = source;
        ExternalUrl = source.AnchorKind == "imdb"
            ? new Uri($"https://www.imdb.com/title/{source.AnchorId}/")
            : source.Kind == "tv"
                ? new Uri($"https://www.themoviedb.org/tv/{source.AnchorId}")
                : new Uri($"https://www.themoviedb.org/movie/{source.AnchorId}");
    }
}
