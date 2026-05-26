using System;
using System.IO;
using PlexRenamer.Bridge.Schemas;
using PlexRenamer.Settings;
using Xunit;

namespace PlexRenamer.Tests;

/// <summary>
/// Settings round-trip tests. The on-disk shape must remain compatible
/// with the Qt app's config.json so a Windows user switching between the
/// Qt build and the WPF build does not lose settings.
/// </summary>
public class SettingsStoreTests : IDisposable
{
    private readonly string _tmpDir;
    private readonly string _configPath;

    public SettingsStoreTests()
    {
        _tmpDir = Path.Combine(Path.GetTempPath(), "plex-renamer-tests-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_tmpDir);
        _configPath = Path.Combine(_tmpDir, "config.json");
    }

    public void Dispose()
    {
        try { Directory.Delete(_tmpDir, recursive: true); } catch { /* best effort */ }
    }

    [Fact]
    public void LoadFromMissingFile_ReturnsEmptyDefaults()
    {
        var store = new SettingsStore(_configPath);
        var settings = store.Load();
        Assert.Null(settings.TmdbApiKey);
        Assert.Null(settings.MoviesRoot);
        Assert.False(settings.CleanupEnabled);
    }

    [Fact]
    public void SaveThenLoad_RoundTrips()
    {
        var store = new SettingsStore(_configPath);
        var original = new PlexRenamer.Bridge.Schemas.Settings
        {
            TmdbApiKey = "abc",
            OmdbApiKey = "def",
            MoviesRoot = @"C:\Movies",
            TvRoot = @"C:\TV",
            CleanupEnabled = true,
            AutoAcceptTopHit = false,
        };
        store.Save(original);

        var loaded = new SettingsStore(_configPath).Load();
        Assert.Equal(original.TmdbApiKey, loaded.TmdbApiKey);
        Assert.Equal(original.OmdbApiKey, loaded.OmdbApiKey);
        Assert.Equal(original.MoviesRoot, loaded.MoviesRoot);
        Assert.Equal(original.TvRoot, loaded.TvRoot);
        Assert.Equal(original.CleanupEnabled, loaded.CleanupEnabled);
        Assert.Equal(original.AutoAcceptTopHit, loaded.AutoAcceptTopHit);
    }

    [Fact]
    public void OnDiskFormat_UsesSnakeCaseKeys()
    {
        var store = new SettingsStore(_configPath);
        store.Save(new PlexRenamer.Bridge.Schemas.Settings
        {
            TmdbApiKey = "x",
            MoviesRoot = @"C:\m",
            CleanupEnabled = true,
        });
        var json = File.ReadAllText(_configPath);
        // The daemon writes/reads snake_case keys (tmdb_api_key, movies_root,
        // cleanup_enabled). The Qt app uses the same convention. If this
        // assertion fails, the WPF build is producing config files the
        // daemon and Qt build can't read.
        Assert.Contains("tmdb_api_key", json);
        Assert.Contains("movies_root", json);
        Assert.Contains("cleanup_enabled", json);
    }
}
