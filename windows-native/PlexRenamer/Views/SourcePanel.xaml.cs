using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Windows.Controls;
using System.Windows.Input;
using PlexRenamer.Bridge.Schemas;

namespace PlexRenamer.Views;

public partial class SourcePanel : UserControl
{
    private readonly ObservableCollection<ResolvedRow> _rows = new();
    private IReadOnlyList<ResolvedGroup> _groups = Array.Empty<ResolvedGroup>();

    /// <summary>Fired when a row is double-clicked. Carries the row.</summary>
    public event EventHandler<ResolvedRow>? RowActivated;

    /// <summary>
    /// Fired when the user wants to assign an anchor to an unanchored TV
    /// group. MainWindow opens the show-anchor picker in response.
    /// </summary>
    public event EventHandler<ResolvedGroup>? GroupAnchorRequested;

    public SourcePanel()
    {
        InitializeComponent();
        RowsListBox.ItemsSource = _rows;
        RowsListBox.MouseDoubleClick += OnRowsListBoxDoubleClick;
    }

    public IReadOnlyList<string> CurrentSourcePaths
        => _rows.Select(r => r.Parsed.SourcePath).ToList();

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
    }

    /// <summary>
    /// Programmatic trigger for the anchor picker — bound to a "Pick show"
    /// affordance in slice 3 / 4 polish. For now, expose so MainWindow can
    /// request it directly when a TV group lands without a candidate.
    /// </summary>
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
}
