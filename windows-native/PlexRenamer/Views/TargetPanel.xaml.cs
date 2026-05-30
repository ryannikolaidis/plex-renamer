using System;
using System.Collections.ObjectModel;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using PlexRenamer.Bridge.Schemas;

namespace PlexRenamer.Views;

public partial class TargetPanel : UserControl
{
    private readonly ObservableCollection<ResolvedGroup> _groups = new();

    public TargetPanel()
    {
        InitializeComponent();
        GroupsListBox.ItemsSource = _groups;
    }

    public void LoadFrom(ParseResolveResult result)
    {
        _groups.Clear();
        foreach (var group in result.Groups)
        {
            _groups.Add(group);
        }
        UpdateHeaderAndEmptyState(result);
    }

    private void UpdateHeaderAndEmptyState(ParseResolveResult result)
    {
        var groupCount = _groups.Count;
        if (groupCount == 0)
        {
            HeaderText.Text = "Target (Plex layout)";
            EmptyStateText.Visibility = Visibility.Visible;
        }
        else
        {
            var fileCount = result.Rows.Count;
            HeaderText.Text = $"Target (Plex layout) · {fileCount} files in {groupCount} groups";
            EmptyStateText.Visibility = Visibility.Collapsed;
        }
    }

    private void OnRevealTargetMenuClick(object sender, RoutedEventArgs e)
    {
        if (GroupsListBox.SelectedItem is ResolvedGroup g)
        {
            // The group's Label is the canonical Plex folder name. We don't
            // know the absolute target path here (that's only resolved at
            // build_plan time), so the best we can do without a round-trip
            // is reveal the user's library root. Open Explorer to nothing
            // — caller-friendly graceful fallback.
            SourcePanel.RevealInExplorer(g.Label);
        }
    }

    private void OnCopyTargetLabelMenuClick(object sender, RoutedEventArgs e)
    {
        if (GroupsListBox.SelectedItem is ResolvedGroup g)
        {
            Clipboard.SetText(g.Label);
        }
    }
}
