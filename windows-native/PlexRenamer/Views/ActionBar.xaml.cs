using System;
using System.Windows.Controls;

namespace PlexRenamer.Views;

public partial class ActionBar : UserControl
{
    public event EventHandler? PreviewClicked;
    public event EventHandler? ApplyClicked;
    public event EventHandler? SettingsClicked;

    public ActionBar()
    {
        InitializeComponent();
    }

    public bool IsPreviewEnabled
    {
        get => PreviewButton.IsEnabled;
        set => PreviewButton.IsEnabled = value;
    }

    public bool IsApplyEnabled
    {
        get => ApplyButton.IsEnabled;
        set => ApplyButton.IsEnabled = value;
    }

    public string ApplyTooltip
    {
        get => ApplyButton.ToolTip as string ?? string.Empty;
        set => ApplyButton.ToolTip = value;
    }

    private void OnPreviewClick(object sender, System.Windows.RoutedEventArgs e)
        => PreviewClicked?.Invoke(this, EventArgs.Empty);

    private void OnApplyClick(object sender, System.Windows.RoutedEventArgs e)
        => ApplyClicked?.Invoke(this, EventArgs.Empty);

    private void OnSettingsClick(object sender, System.Windows.RoutedEventArgs e)
        => SettingsClicked?.Invoke(this, EventArgs.Empty);
}
