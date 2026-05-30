using System;
using System.Globalization;
using System.Windows;
using System.Windows.Data;
using PlexRenamer.Bridge.Schemas;

namespace PlexRenamer.Views.Converters;

/// <summary>
/// Maps <see cref="ResolvedRow.Skip"/> to row opacity. Skipped rows
/// render dimmed so the user can see at a glance which rows are out
/// of the next apply.
/// </summary>
public sealed class SkipToOpacityConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        return value is true ? 0.45 : 1.0;
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
    {
        throw new NotSupportedException();
    }
}

/// <summary>
/// Pulls the <see cref="Candidate.Confidence"/> out of a row's nullable
/// <see cref="ResolvedRow.Candidate"/> so the ConfidenceBadge can bind
/// to a row directly without the XAML needing a null-walk.
/// </summary>
public sealed class CandidateToConfidenceConverter : IValueConverter
{
    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        return value is Candidate c ? c.Confidence : 0.0;
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
    {
        throw new NotSupportedException();
    }
}

/// <summary>
/// Maps a nullable Candidate to <see cref="Visibility.Visible"/> when
/// non-null, <see cref="Visibility.Collapsed"/> when null. Set
/// <see cref="Inverted"/> to flip the polarity (visible when null) for
/// the "no anchor" placeholder badge.
/// </summary>
public sealed class NullCandidateToVisibilityConverter : IValueConverter
{
    public bool Inverted { get; set; }

    public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
    {
        var hasCandidate = value is Candidate;
        if (Inverted)
        {
            return hasCandidate ? Visibility.Collapsed : Visibility.Visible;
        }
        return hasCandidate ? Visibility.Visible : Visibility.Collapsed;
    }

    public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
    {
        throw new NotSupportedException();
    }
}
