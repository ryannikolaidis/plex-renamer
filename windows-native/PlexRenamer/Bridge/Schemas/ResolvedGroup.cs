using System.Collections.Generic;

namespace PlexRenamer.Bridge.Schemas;

/// <summary>
/// Mirrors the daemon's <c>Group</c> shape. A movie's group has exactly
/// one row; a TV group has 1..N rows that share a show.
/// </summary>
public sealed record ResolvedGroup
{
    public required string GroupKey { get; init; }
    public required string Kind { get; init; }
    public required string Label { get; init; }
    public required IReadOnlyList<string> RowIds { get; init; }
}
