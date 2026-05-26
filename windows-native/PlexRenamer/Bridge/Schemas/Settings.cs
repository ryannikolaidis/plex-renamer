namespace PlexRenamer.Bridge.Schemas;

/// <summary>
/// Mirrors the daemon's <c>Settings</c> shape from
/// <c>docs/win-native-bridge.md</c>. Snake-case JSON keys are mapped by
/// <see cref="System.Text.Json.JsonNamingPolicy.SnakeCaseLower"/> in
/// <see cref="EngineClient"/>.
/// </summary>
public sealed record Settings
{
    public string? TmdbApiKey { get; init; }
    public string? OmdbApiKey { get; init; }
    public string? MoviesRoot { get; init; }
    public string? TvRoot { get; init; }
    public bool CleanupEnabled { get; init; }
    public bool AutoAcceptTopHit { get; init; }
}
