using System.Collections.ObjectModel;
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
    }
}
