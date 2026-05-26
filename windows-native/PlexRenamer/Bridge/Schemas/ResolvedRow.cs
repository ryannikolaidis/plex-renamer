using System.Collections.Generic;

namespace PlexRenamer.Bridge.Schemas;

/// <summary>
/// Mirrors the daemon's <c>Row</c> shape (see <c>docs/win-native-bridge.md</c>).
/// Carries one source file + its current resolved state across method calls.
/// </summary>
public sealed record ResolvedRow
{
    public required string RowId { get; init; }
    public required ParsedFields Parsed { get; init; }
    public Candidate? Candidate { get; init; }
    public string? ShowNameHint { get; init; }
    public required string GroupKey { get; init; }
    public bool Skip { get; init; }
    public string? ManualTitle { get; init; }
    public int? ManualYear { get; init; }
    public int? ManualSeason { get; init; }
    public int? ManualEpisode { get; init; }
    public string? ManualEdition { get; init; }
    public string? ImdbIdOverride { get; init; }
    public string? AnchorKindOverride { get; init; }
}

/// <summary>Mirrors the daemon's <c>ParseResult</c> shape.</summary>
public sealed record ParsedFields
{
    public required string SourcePath { get; init; }
    public required string Kind { get; init; }
    public string? TitleCandidate { get; init; }
    public int? Year { get; init; }
    public int? Season { get; init; }
    public int? Episode { get; init; }
    public int? EpisodeEnd { get; init; }
    public string? EpisodeTitle { get; init; }
    public IReadOnlyList<string> EditionTokens { get; init; } = new List<string>();
    public IReadOnlyList<string> QualityTokens { get; init; } = new List<string>();
    public string? GroupTag { get; init; }
    public string? PartMarker { get; init; }
    public required string RawFilename { get; init; }
    public IReadOnlyList<string> ParentDirs { get; init; } = new List<string>();
    public SkipReason? SkipReason { get; init; }
    public IReadOnlyList<Sidecar> Sidecars { get; init; } = new List<Sidecar>();
}

public sealed record SkipReason
{
    public required string Reason { get; init; }
    public string? Detail { get; init; }
}

public sealed record Sidecar
{
    public required string Path { get; init; }
    public required string Kind { get; init; }
    public string? Language { get; init; }
    public IReadOnlyList<string> Modifiers { get; init; } = new List<string>();
}

public sealed record Candidate
{
    public required string AnchorKind { get; init; }
    public required string AnchorId { get; init; }
    public required string Kind { get; init; }
    public required string Title { get; init; }
    public int? Year { get; init; }
    public double Confidence { get; init; }
    public IReadOnlyList<Episode> EpisodeList { get; init; } = new List<Episode>();
}

public sealed record Episode
{
    public required int Season { get; init; }
    public required int Episode_ { get; init; } // 'episode' would clash with the type name
    public required string Title { get; init; }
    public string? AirDate { get; init; }
}

/// <summary>Aggregate response from <c>parse_and_resolve</c>.</summary>
public sealed record ParseResolveResult
{
    public required IReadOnlyList<ResolvedRow> Rows { get; init; }
    public required IReadOnlyList<ResolvedGroup> Groups { get; init; }
    public required string InputRoot { get; init; }
    public IReadOnlyList<ResolveError> Errors { get; init; } = new List<ResolveError>();
}

public sealed record ResolveError
{
    public required string SourcePath { get; init; }
    public required string Message { get; init; }
}
