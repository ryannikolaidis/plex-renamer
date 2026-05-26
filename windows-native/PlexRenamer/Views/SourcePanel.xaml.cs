using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Windows.Controls;
using PlexRenamer.Bridge.Schemas;

namespace PlexRenamer.Views;

public partial class SourcePanel : UserControl
{
    private readonly ObservableCollection<ResolvedRow> _rows = new();

    public SourcePanel()
    {
        InitializeComponent();
        RowsListBox.ItemsSource = _rows;
    }

    public IReadOnlyList<string> CurrentSourcePaths
        => _rows.Select(r => r.Parsed.SourcePath).ToList();

    public void LoadFrom(ParseResolveResult result)
    {
        _rows.Clear();
        foreach (var row in result.Rows)
        {
            _rows.Add(row);
        }
    }
}
