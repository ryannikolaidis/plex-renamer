using System.Windows;
using Wpf.Ui.Controls;

namespace PlexRenamer.Views;

/// <summary>
/// Lightweight modal for a single-field edit. Used by the SourcePanel
/// context-menu's "Override metadata" submenu and "Set IMDb ID…" entry,
/// where a full <see cref="EditPane"/> would be overkill.
/// </summary>
public partial class SingleValuePrompt : FluentWindow
{
    public string? EnteredValue { get; private set; }

    public SingleValuePrompt(string title, string description, string placeholder, string initialValue)
    {
        InitializeComponent();
        Title = title;
        TitleBarControl.Title = title;
        DescriptionText.Text = description;
        ValueBox.PlaceholderText = placeholder;
        ValueBox.Text = initialValue;
        Loaded += (_, _) =>
        {
            ValueBox.Focus();
            ValueBox.SelectAll();
        };
    }

    private void OnOkClick(object sender, RoutedEventArgs e)
    {
        EnteredValue = ValueBox.Text;
        DialogResult = true;
    }

    private void OnCancelClick(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
    }
}
