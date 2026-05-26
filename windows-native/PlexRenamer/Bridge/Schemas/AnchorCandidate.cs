using System.Collections.Generic;

namespace PlexRenamer.Bridge.Schemas;

/// <summary>
/// Result of <c>iterate_anchor_search</c>. The picker dialog reads
/// <see cref="VariantUsed"/> / <see cref="VariantOriginal"/> to surface
/// "We tried 'Foo Bar' but found results for 'Foo'" when the cleaned-
/// variant retry chain produces hits the literal query didn't.
/// </summary>
public sealed record AnchorSearchResult
{
    public required IReadOnlyList<Candidate> Candidates { get; init; }
    public string? VariantUsed { get; init; }
    public string? VariantOriginal { get; init; }
    public IReadOnlyList<string> VariantsTried { get; init; } = new List<string>();
}

/// <summary>Result of <c>select_anchor</c>.</summary>
public sealed record SelectAnchorResult
{
    public required IReadOnlyList<ResolvedRow> Rows { get; init; }
    public IReadOnlyList<ResolveError> Errors { get; init; } = new List<ResolveError>();
}

/// <summary>Result of <c>edit_row</c>.</summary>
public sealed record EditRowResult
{
    public required IReadOnlyList<ResolvedRow> Rows { get; init; }
}

/// <summary>
/// Override fields passed to <c>edit_row</c>. Null fields are not sent;
/// the daemon's <c>edit_row</c> applies only the keys actually present
/// in the params dict, so the C# side must omit null/unchanged keys.
/// System.Text.Json's <c>DefaultIgnoreCondition.WhenWritingNull</c> in
/// the EngineClient options handles this automatically.
/// </summary>
public sealed record EditRowOverrides
{
    public string? ManualTitle { get; init; }
    public int? ManualYear { get; init; }
    public int? ManualSeason { get; init; }
    public int? ManualEpisode { get; init; }
    public string? ManualEdition { get; init; }
    public string? ImdbIdOverride { get; init; }
    public string? AnchorKindOverride { get; init; }
    public string? ShowNameHint { get; init; }
    public bool? Skip { get; init; }
    public Candidate? Candidate { get; init; }
}

/// <summary>Result of <c>search_tmdb_free</c>.</summary>
public sealed record TmdbSearchResult
{
    public required IReadOnlyList<Candidate> Candidates { get; init; }
    public string? Error { get; init; }
}

/// <summary>Result of <c>find_by_imdb</c>. The candidate is never null
/// — TMDB miss synthesizes an IMDb-anchored Candidate per the daemon's
/// resolve_imdb_for_row port from the Qt orchestrator.</summary>
public sealed record FindByImdbResult
{
    public required Candidate Candidate { get; init; }
    public IReadOnlyList<ResolveError> Errors { get; init; } = new List<ResolveError>();
}
