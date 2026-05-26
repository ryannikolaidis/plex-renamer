namespace PlexRenamer.Bridge.Schemas;

/// <summary>
/// Result of <c>build_plan</c>. The shell consumes <see cref="Plan"/>
/// directly; the apply-time UI surfaces <c>Plan.Collisions</c> to the
/// collision-review dialog when non-empty.
/// </summary>
public sealed record BuildPlanResult
{
    public required PlanOp Plan { get; init; }
}

/// <summary>
/// Result of <c>undo_batch</c>. The daemon emits this flat (no envelope)
/// per the protocol doc.
/// </summary>
public sealed record UndoReport
{
    public required int Reverted { get; init; }
    public required int MovedToReview { get; init; }
    public string? ReviewDir { get; init; }
    public required bool SourcesRecoverable { get; init; }
}
