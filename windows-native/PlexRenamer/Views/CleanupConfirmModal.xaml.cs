using System.Collections.Generic;
using System.Windows;
using Wpf.Ui.Controls;

namespace PlexRenamer.Views;

/// <summary>
/// Hard-required confirmation modal before any source-cleanup apply.
/// INVARIANTS.md mandates an explicit checkbox before deletion; closing
/// or unchecking aborts cleanup entirely. The cancel button doesn't
/// abort the WHOLE apply — it aborts cleanup, leaving the user the
/// option to apply without cleanup (source files survive).
/// </summary>
public partial class CleanupConfirmModal : FluentWindow
{
    public CleanupConfirmModal(IReadOnlyList<string> paths)
    {
        InitializeComponent();
        PathsList.ItemsSource = paths;
    }

    private void OnUnderstandToggled(object sender, RoutedEventArgs e)
    {
        ProceedButton.IsEnabled = UnderstandCheckbox.IsChecked == true;
    }

    private void OnProceedClick(object sender, RoutedEventArgs e)
    {
        DialogResult = true;
    }

    private void OnCancelClick(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
    }
}
