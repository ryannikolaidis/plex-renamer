using System.Collections.Generic;
using System.ComponentModel;
using System.Linq;
using System.Windows;
using PlexRenamer.Bridge.Schemas;
using Wpf.Ui.Controls;

namespace PlexRenamer.Views;

/// <summary>
/// Backing view-model for one source row in a collision group. Renders
/// as a radio button — exactly one source per collision is the
/// "winner" and the rest get marked as Skip in the next preview.
/// </summary>
public sealed class CollisionSourceChoice : INotifyPropertyChanged
{
    private bool _isWinner;

    public required string GroupName { get; init; }
    public required string SourcePath { get; init; }

    public bool IsWinner
    {
        get => _isWinner;
        set
        {
            if (_isWinner == value) return;
            _isWinner = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(IsWinner)));
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;
}

/// <summary>One collision row in the view-model.</summary>
public sealed class CollisionView
{
    public required string Target { get; init; }
    public required string Reason { get; init; }
    public required IReadOnlyList<CollisionSourceChoice> SourceChoices { get; init; }
}

/// <summary>
/// Modal shown when build_plan returns a non-empty collisions list.
/// The user picks ONE source per collision; the others get marked as
/// Skip in the next preview. A "Skip all" escape hatch covers the
/// case where the user wants to drop the colliding inputs entirely.
/// </summary>
public partial class CollisionReviewDialog : FluentWindow
{
    public IReadOnlyList<Collision> Collisions { get; }

    /// <summary>The source paths the user chose to skip (the losing sources from each collision, OR every source if the user clicks "Skip all").</summary>
    public IReadOnlyList<string> SkippedSourcePaths { get; private set; } = System.Array.Empty<string>();

    private readonly List<CollisionView> _viewModels;

    public CollisionReviewDialog(IReadOnlyList<Collision> collisions)
    {
        Collisions = collisions;
        InitializeComponent();
        _viewModels = new List<CollisionView>(collisions.Count);
        var groupIdx = 0;
        var totalSources = 0;
        foreach (var c in collisions)
        {
            var groupName = $"collision_{groupIdx++}";
            var choices = c.Sources.Select((src, i) => new CollisionSourceChoice
            {
                GroupName = groupName,
                SourcePath = src,
                IsWinner = i == 0, // default: first source wins
            }).ToList();
            _viewModels.Add(new CollisionView
            {
                Target = c.Target,
                Reason = c.Reason,
                SourceChoices = choices,
            });
            totalSources += c.Sources.Count;
        }
        var label = collisions.Count == 1
            ? $"1 collision affecting {totalSources} sources"
            : $"{collisions.Count} collisions affecting {totalSources} sources";
        HeadlineText.Text = label;
        CollisionsItems.ItemsSource = _viewModels;
    }

    private void OnApplyChoicesClick(object sender, RoutedEventArgs e)
    {
        // Every non-winner source becomes a skip.
        var skips = new List<string>();
        foreach (var c in _viewModels)
        {
            foreach (var choice in c.SourceChoices)
            {
                if (!choice.IsWinner)
                {
                    skips.Add(choice.SourcePath);
                }
            }
        }
        SkippedSourcePaths = skips;
        DialogResult = true;
    }

    private void OnSkipAllClick(object sender, RoutedEventArgs e)
    {
        SkippedSourcePaths = Collisions.SelectMany(c => c.Sources).Distinct().ToList();
        DialogResult = true;
    }

    private void OnCancelClick(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
    }
}
