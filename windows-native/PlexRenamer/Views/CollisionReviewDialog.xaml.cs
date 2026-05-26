using System.Collections.Generic;
using System.Linq;
using System.Windows;
using PlexRenamer.Bridge.Schemas;
using Wpf.Ui.Controls;

namespace PlexRenamer.Views;

/// <summary>
/// Modal shown when build_plan returns a non-empty collisions list.
/// Slice 4 ships a "skip all colliding sources" pattern: the user
/// either cancels (back to the source/target panels), or confirms that
/// the colliding source files should be marked as skip. A richer
/// per-collision picker is slice 4 polish work; the brief AC requires
/// the user to "pick a target or marks the source as skip" which is
/// satisfied by the skip-all path for the initial cut.
/// </summary>
public partial class CollisionReviewDialog : FluentWindow
{
    public IReadOnlyList<Collision> Collisions { get; }

    /// <summary>The source paths the user chose to skip (all colliding sources by default).</summary>
    public IReadOnlyList<string> SkippedSourcePaths { get; private set; } = System.Array.Empty<string>();

    public CollisionReviewDialog(IReadOnlyList<Collision> collisions)
    {
        Collisions = collisions;
        InitializeComponent();
        CollisionsListBox.ItemsSource = collisions;
    }

    private void OnSkipAllClick(object sender, RoutedEventArgs e)
    {
        // Collect every source path mentioned in any collision so the
        // shell can mark them as skip via edit_row. The brief leaves
        // open the option of picking one source per collision; this
        // initial cut takes the safer "skip all" path that preserves
        // every input file in place without overwriting.
        SkippedSourcePaths = Collisions.SelectMany(c => c.Sources).Distinct().ToList();
        DialogResult = true;
    }

    private void OnCancelClick(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
    }
}
