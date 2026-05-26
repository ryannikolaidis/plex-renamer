using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using PlexRenamer.Bridge.Schemas;

namespace PlexRenamer.Bridge;

/// <summary>
/// JSON-RPC client surface the WPF shell talks to. Wraps the Python
/// sidecar daemon (<c>plex-renamer-engined</c>) at process boundary.
/// Tests substitute fake implementations of this interface without
/// spawning a real subprocess.
/// </summary>
/// <remarks>
/// The protocol contract lives in <c>docs/win-native-bridge.md</c> at the
/// repo root. POCO records under <see cref="PlexRenamer.Bridge.Schemas"/>
/// mirror the wire shapes documented there. If a record drifts from the
/// doc, the doc wins; update the record.
/// </remarks>
public interface IEngineClient : IAsyncDisposable
{
    /// <summary>
    /// Spawn the sidecar subprocess and prepare it for requests. Idempotent:
    /// a second call after a successful first call is a no-op. Throws on
    /// spawn failure (missing binary, dev-mode misconfiguration).
    /// </summary>
    Task StartAsync(CancellationToken cancellationToken = default);

    /// <summary>
    /// Fires when the sidecar process exits unexpectedly (without a
    /// preceding shutdown call). UI subscribes to surface a modal error
    /// + disable Preview/Apply until <see cref="RestartAsync"/> succeeds.
    /// </summary>
    event EventHandler<EngineExitedEventArgs>? UnexpectedExit;

    /// <summary>
    /// Tear down a dead sidecar and spawn a fresh one. Returns when the
    /// new sidecar is ready to accept requests.
    /// </summary>
    Task RestartAsync(CancellationToken cancellationToken = default);

    // ---- One-shot RPC methods ----

    Task<Settings> GetSettingsAsync(CancellationToken cancellationToken = default);

    Task<Settings> SaveSettingsAsync(Settings settings, CancellationToken cancellationToken = default);

    Task<ParseResolveResult> ParseAndResolveAsync(
        IReadOnlyList<string> paths,
        Settings? settings,
        CancellationToken cancellationToken = default);

    // Other one-shot methods (search_tmdb_free, find_by_imdb,
    // iterate_anchor_search, select_anchor, edit_row, build_plan,
    // undo_batch) are added in slices 3 and 4 as their UI surfaces land.
    // Slice 2's scope is parse+display + settings, so only the methods
    // those flows need are exposed here.

    // ---- Streaming RPC method ----

    /// <summary>
    /// Stream apply_plan's progress notifications, then yield the final
    /// RunReport as the last item in the sequence. The shell awaits the
    /// last element to know the apply completed.
    /// </summary>
    /// <remarks>
    /// Slice 2 declares this method to lock the bridge's streaming
    /// surface; slice 4 wires the Apply button to actually call it.
    /// </remarks>
    IAsyncEnumerable<ApplyEvent> ApplyPlanAsync(
        PlanOp plan,
        bool cleanup,
        bool verifyHash,
        Settings? settings,
        CancellationToken cancellationToken = default);
}

public sealed class EngineExitedEventArgs : EventArgs
{
    public required int ExitCode { get; init; }
    public required string? Stderr { get; init; }
}

/// <summary>
/// One event in the apply_plan stream. The shell distinguishes
/// progress notifications (<see cref="EventKind"/> in
/// <c>op_started | op_verified | op_failed</c>) from the terminal
/// RunReport (<see cref="EventKind"/> == <c>done</c>).
/// </summary>
public sealed record ApplyEvent
{
    public required string EventKind { get; init; }
    public int? OpIndex { get; init; }
    public string? Source { get; init; }
    public string? Target { get; init; }
    public long? Bytes { get; init; }
    public string? Error { get; init; }
    /// <summary>Non-null only when <see cref="EventKind"/> == <c>done</c>.</summary>
    public RunReport? Result { get; init; }
}

public sealed record RunReport
{
    public required int Succeeded { get; init; }
    public required int Failed { get; init; }
    public required int Skipped { get; init; }
    public required bool CleanupRan { get; init; }
    public string? JournalPath { get; init; }
    public IReadOnlyList<string> ErrorMessages { get; init; } = Array.Empty<string>();
}
