using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace PlexRenamer.Views;

/// <summary>
/// Three-band confidence indicator. Maps the daemon's raw confidence
/// float to green / yellow / red per INVARIANTS.md's bands:
/// >= 0.85 auto-accept (green), >= 0.60 needs-review (yellow), &lt; 0.60
/// unresolved (red).
/// </summary>
public partial class ConfidenceBadge : UserControl
{
    public static readonly DependencyProperty ConfidenceProperty =
        DependencyProperty.Register(
            nameof(Confidence),
            typeof(double),
            typeof(ConfidenceBadge),
            new PropertyMetadata(0.0, OnConfidenceChanged));

    public double Confidence
    {
        get => (double)GetValue(ConfidenceProperty);
        set => SetValue(ConfidenceProperty, value);
    }

    public ConfidenceBadge()
    {
        InitializeComponent();
        UpdateBadge(0.0);
    }

    private static void OnConfidenceChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
    {
        if (d is ConfidenceBadge badge && e.NewValue is double v)
        {
            badge.UpdateBadge(v);
        }
    }

    private void UpdateBadge(double confidence)
    {
        if (confidence >= 0.85)
        {
            BadgeText.Text = "AUTO";
            BadgeBorder.Background = new SolidColorBrush(Color.FromRgb(0x4c, 0xaf, 0x50));
            BadgeText.Foreground = Brushes.White;
        }
        else if (confidence >= 0.60)
        {
            BadgeText.Text = "REVIEW";
            BadgeBorder.Background = new SolidColorBrush(Color.FromRgb(0xff, 0xc1, 0x07));
            BadgeText.Foreground = Brushes.Black;
        }
        else
        {
            BadgeText.Text = "UNRESOLVED";
            BadgeBorder.Background = new SolidColorBrush(Color.FromRgb(0xf4, 0x43, 0x36));
            BadgeText.Foreground = Brushes.White;
        }
    }
}
