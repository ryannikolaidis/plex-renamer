using System;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;
using PlexRenamer.Bridge.Schemas;

namespace PlexRenamer.Configuration;

/// <summary>
/// Reads / writes the same on-disk config file the Qt app uses
/// (<c>%APPDATA%\plex-renamer\config.json</c>). The shared format means a
/// Windows user who switches between the legacy Qt build and the WPF
/// build does not lose settings.
/// </summary>
/// <remarks>
/// The daemon ALSO reads / writes this file via its own <c>get_settings</c>
/// / <c>save_settings</c> RPC methods. The shell exclusively uses the
/// daemon path during a session (so cache invalidation and TMDB-client
/// rebuilds stay coherent). This <see cref="SettingsStore"/> is here for
/// (a) reading the file at startup BEFORE the daemon is up to know
/// whether to show the first-run TMDB-key prompt, and (b) tests that
/// want to assert the on-disk file shape directly.
/// </remarks>
public sealed class SettingsStore
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        WriteIndented = true,
    };

    private readonly string _configPath;

    public SettingsStore() : this(DefaultConfigPath()) { }

    /// <summary>Test constructor with an explicit path.</summary>
    public SettingsStore(string configPath)
    {
        _configPath = configPath;
    }

    public string ConfigPath => _configPath;

    /// <summary>Read settings from disk; return empty defaults if the file is missing.</summary>
    public Bridge.Schemas.Settings Load()
    {
        if (!File.Exists(_configPath))
        {
            return new Bridge.Schemas.Settings();
        }
        var json = File.ReadAllText(_configPath);
        return JsonSerializer.Deserialize<Bridge.Schemas.Settings>(json, JsonOptions)
            ?? new Bridge.Schemas.Settings();
    }

    public async Task<Bridge.Schemas.Settings> LoadAsync()
    {
        if (!File.Exists(_configPath))
        {
            return new Bridge.Schemas.Settings();
        }
        await using var stream = File.OpenRead(_configPath);
        var settings = await JsonSerializer.DeserializeAsync<Bridge.Schemas.Settings>(stream, JsonOptions)
            .ConfigureAwait(false);
        return settings ?? new Bridge.Schemas.Settings();
    }

    /// <summary>Persist settings to disk. Creates the config directory if missing.</summary>
    public void Save(Bridge.Schemas.Settings settings)
    {
        var dir = Path.GetDirectoryName(_configPath);
        if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir))
        {
            Directory.CreateDirectory(dir);
        }
        File.WriteAllText(_configPath, JsonSerializer.Serialize(settings, JsonOptions));
    }

    public static string DefaultConfigPath()
    {
        // Honor the daemon's PLEX_RENAMER_CONFIG_DIR override for symmetry
        // with the Python side. In production this env var is unset; the
        // shell defaults to %APPDATA%\plex-renamer\config.json.
        var configDir = Environment.GetEnvironmentVariable("PLEX_RENAMER_CONFIG_DIR");
        if (string.IsNullOrEmpty(configDir))
        {
            var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            configDir = Path.Combine(appData, "plex-renamer");
        }
        return Path.Combine(configDir, "config.json");
    }
}
