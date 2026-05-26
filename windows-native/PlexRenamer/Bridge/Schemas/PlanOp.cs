using System.Collections.Generic;

namespace PlexRenamer.Bridge.Schemas;

/// <summary>
/// Mirrors the daemon's <c>RenamePlan</c> shape on the wire. Named
/// <c>PlanOp</c> rather than <c>RenamePlan</c> to avoid colliding with the
/// .NET type system around <c>Plan</c> conventions elsewhere in the
/// shell; the JSON key remains <c>plan</c> at the request boundary.
/// </summary>
public sealed record PlanOp
{
    public required IReadOnlyList<RenameOp> Ops { get; init; }
    public IReadOnlyList<Collision> Collisions { get; init; } = new List<Collision>();
    public IReadOnlyList<SkippedEntry> Skipped { get; init; } = new List<SkippedEntry>();
    public required string MoviesRoot { get; init; }
    public required string TvRoot { get; init; }
    public required string InputRoot { get; init; }
    public bool ApplyEditions { get; init; }
    public IReadOnlyList<string> Warnings { get; init; } = new List<string>();
}

public sealed record RenameOp
{
    public required string Source { get; init; }
    public required string Target { get; init; }
    public required string Kind { get; init; }
    public required string Anchor { get; init; }
    public string? Edition { get; init; }
    public double Confidence { get; init; }
    public IReadOnlyList<IReadOnlyList<string>> Sidecars { get; init; }
        = new List<IReadOnlyList<string>>();
    public IReadOnlyList<string> Warnings { get; init; } = new List<string>();
    public IReadOnlyList<string> DetectedEditions { get; init; } = new List<string>();
}

public sealed record Collision
{
    public required string Target { get; init; }
    public required IReadOnlyList<string> Sources { get; init; }
    public required string Reason { get; init; }
}

public sealed record SkippedEntry
{
    public required string Path { get; init; }
    public required string Reason { get; init; }
}
