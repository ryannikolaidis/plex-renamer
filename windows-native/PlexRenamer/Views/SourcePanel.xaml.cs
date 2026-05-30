using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using PlexRenamer.Bridge.Schemas;

namespace PlexRenamer.Views;

public sealed class RowOverrideRequest
{
    public required IReadOnlyList<ResolvedRow> Rows { get; init; }
    public required string Kind { get; init; }
}

public sealed class RowRevealRequest
{
    public required ResolvedRow Row { get; init; }
}

public partial class SourcePanel : UserControl
{
    private readonly ObservableCollection<ResolvedRow> _rows = new();
    private IReadOnlyList<ResolvedGroup> _groups = Array.Empty<ResolvedGroup>();

    /// <summary>Fired when a row is double-clicked OR "Edit row…" is chosen from the context menu.</summary>
    public event EventHandler<ResolvedRow>? RowActivated;

    /// <summary>Fired when the user wants to assign an anchor to an unanchored TV group.</summary>
    public event EventHandler<ResolvedGroup>? GroupAnchorRequested;

    /// <summary>Fired when the user picks "Search TMDB by title…" on a row.</summary>
    public event EventHandler<ResolvedRow>? SearchTmdbRequested;

    /// <summary>Fired when the user picks "Set IMDb ID…" on a row.</summary>
    public event EventHandler<ResolvedRow>? SetImdbRequested;

    /// <summary>
    /// Fired when the user picks an "Override metadata" submenu item.
    /// <see cref="RowOverrideRequest.Kind"/> is one of
    /// <c>title | year | season | episode | edition</c>.
    /// </summary>
    public event EventHandler<RowOverrideRequest>? OverrideRequested;

    /// <summary>
    /// Fired when the user toggles "Skip this row" from the context menu.
    /// Affects every currently-selected row.
    /// </summary>
    public event EventHandler<IReadOnlyList<ResolvedRow>>? ToggleSkipRequested;

    public SourcePanel()
    {
        InitializeComponent();
        RowsListBox.ItemsSource = _rows;
        RowsListBox.MouseDoubleClick += OnRowsListBoxDoubleClick;
        RowsListBox.KeyDown += OnRowsListBoxKeyDown;
    }

    public IReadOnlyList<string> CurrentSourcePaths
        => _rows.Select(r => r.Parsed.SourcePath).ToList();

    public IReadOnlyList<ResolvedRow> SelectedRows
        => RowsListBox.SelectedItems.OfType<ResolvedRow>().ToList();

    public void LoadFrom(ParseResolveResult result)
    {
        LoadRows(result.Rows, result.Groups);
    }

    public void LoadRows(IReadOnlyList<ResolvedRow> rows, IReadOnlyList<ResolvedGroup> groups)
    {
        _rows.Clear();
        foreach (var row in rows)
        {
            _rows.Add(row);
        }
        _groups = groups;
        UpdateHeaderAndEmptyState();
    }

    private void UpdateHeaderAndEmptyState()
    {
        var total = _rows.Count;
        var skipped = _rows.Count(r => r.Skip);
        if (total == 0)
        {
            HeaderText.Text = "Source";
            EmptyStateText.Visibility = Visibility.Visible;
        }
        else
        {
            HeaderText.Text = skipped > 0
                ? $"Source · {total} files · {skipped} skipped"
                : $"Source · {total} files";
            EmptyStateText.Visibility = Visibility.Collapsed;
        }
    }

    public void RequestAnchorForGroup(string groupKey)
    {
        var group = _groups.FirstOrDefault(g => g.GroupKey == groupKey);
        if (group != null)
        {
            GroupAnchorRequested?.Invoke(this, group);
        }
    }

    private void OnRowsListBoxDoubleClick(object sender, MouseButtonEventArgs e)
    {
        if (RowsListBox.SelectedItem is ResolvedRow row)
        {
            RowActivated?.Invoke(this, row);
        }
    }

    private void OnRowsListBoxKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter && RowsListBox.SelectedItem is ResolvedRow enterRow)
        {
            RowActivated?.Invoke(this, enterRow);
            e.Handled = true;
        }
        else if (e.Key == Key.Delete && SelectedRows.Count > 0)
        {
            ToggleSkipRequested?.Invoke(this, SelectedRows);
            e.Handled = true;
        }
    }

    private void OnContextMenuOpened(object sender, RoutedEventArgs e)
    {
        var selected = SelectedRows;
        var single = selected.Count == 1 ? selected[0] : null;
        var anyTv = selected.Any(r => string.Equals(r.Parsed.Kind, "tv", StringComparison.OrdinalIgnoreCase));
        var hasAnyUnanchoredTvGroup = false;
        if (single != null
            && string.Equals(single.Parsed.Kind, "tv", StringComparison.OrdinalIgnoreCase))
        {
            // A TV group is unanchored if no row in the group carries a
            // resolved Candidate. ResolvedGroup itself doesn't track anchor
            // state — it lives on the row's Candidate field.
            var groupKey = single.GroupKey;
            hasAnyUnanchoredTvGroup = !_rows.Any(r => r.GroupKey == groupKey && r.Candidate != null);
        }

        // Single-row only: open the full editor + search TMDB + set IMDb.
        // Multi-select keeps Skip + Override-metadata.
        EditRowItem.IsEnabled = single != null;
        SearchTmdbItem.IsEnabled = single != null;
        SetImdbItem.IsEnabled = single != null;
        PickShowItem.Visibility = hasAnyUnanchoredTvGroup ? Visibility.Visible : Visibility.Collapsed;
        OverrideSeasonItem.Visibility = anyTv ? Visibility.Visible : Visibility.Collapsed;
        OverrideEpisodeItem.Visibility = anyTv ? Visibility.Visible : Visibility.Collapsed;

        if (selected.Count > 0)
        {
            ToggleSkipItem.IsChecked = selected.All(r => r.Skip);
        }
    }

    private void OnEditRowMenuClick(object sender, RoutedEventArgs e)
    {
        if (SelectedRows.Count == 1)
        {
            RowActivated?.Invoke(this, SelectedRows[0]);
        }
    }

    private void OnSearchTmdbMenuClick(object sender, RoutedEventArgs e)
    {
        if (SelectedRows.Count == 1)
        {
            SearchTmdbRequested?.Invoke(this, SelectedRows[0]);
        }
    }

    private void OnSetImdbMenuClick(object sender, RoutedEventArgs e)
    {
        if (SelectedRows.Count == 1)
        {
            SetImdbRequested?.Invoke(this, SelectedRows[0]);
        }
    }

    private void OnPickShowMenuClick(object sender, RoutedEventArgs e)
    {
        if (SelectedRows.Count == 1)
        {
            RequestAnchorForGroup(SelectedRows[0].GroupKey);
        }
    }

    private void OnOverrideTitleMenuClick(object sender, RoutedEventArgs e)
        => EmitOverride("title");
    private void OnOverrideYearMenuClick(object sender, RoutedEventArgs e)
        => EmitOverride("year");
    private void OnOverrideSeasonMenuClick(object sender, RoutedEventArgs e)
        => EmitOverride("season");
    private void OnOverrideEpisodeMenuClick(object sender, RoutedEventArgs e)
        => EmitOverride("episode");
    private void OnOverrideEditionMenuClick(object sender, RoutedEventArgs e)
        => EmitOverride("edition");

    private void EmitOverride(string kind)
    {
        if (SelectedRows.Count == 0) return;
        OverrideRequested?.Invoke(this, new RowOverrideRequest
        {
            Rows = SelectedRows,
            Kind = kind,
        });
    }

    private void OnToggleSkipMenuClick(object sender, RoutedEventArgs e)
    {
        if (SelectedRows.Count > 0)
        {
            ToggleSkipRequested?.Invoke(this, SelectedRows);
        }
    }

    private void OnRevealSourceMenuClick(object sender, RoutedEventArgs e)
    {
        foreach (var row in SelectedRows)
        {
            RevealInExplorer(row.Parsed.SourcePath);
        }
    }

    private void OnCopySourcePathMenuClick(object sender, RoutedEventArgs e)
    {
        var paths = SelectedRows.Select(r => r.Parsed.SourcePath).ToList();
        if (paths.Count > 0)
        {
            Clipboard.SetText(string.Join(Environment.NewLine, paths));
        }
    }

    internal static void RevealInExplorer(string path)
    {
        if (string.IsNullOrEmpty(path)) return;
        try
        {
            // /select, highlights the file in its containing folder; if the
            // path is itself a folder Explorer just opens it.
            System.Diagnostics.Process.Start("explorer.exe", $"/select,\"{path}\"");
        }
        catch
        {
            // Best-effort — Explorer reveal failures aren't worth a modal.
        }
    }
}
