using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Threading;
using System.Threading.Channels;
using System.Threading.Tasks;
using PlexRenamer.Bridge.Schemas;

namespace PlexRenamer.Bridge;

/// <summary>
/// Production <see cref="IEngineClient"/> that spawns the Python sidecar
/// subprocess and exchanges newline-delimited JSON-RPC 2.0 messages over
/// stdin / stdout.
/// </summary>
/// <remarks>
/// Dev vs installed binary lookup. In an installed bundle the sidecar
/// <c>plex-renamer-engined.exe</c> sits next to <c>PlexRenamer.exe</c> in
/// <c>Program Files\plex-renamer\gui\</c>; <see cref="ResolveSidecarCommand"/>
/// returns that path. In a source-tree run (a developer running
/// <c>dotnet run</c> from the worktree) the binary doesn't exist; the
/// method falls back to launching <c>uv run plex-renamer-engined</c> from
/// the repo root resolved via the <c>PLEX_RENAMER_REPO_ROOT</c> env var.
/// The fallback is documented in <c>windows-native/README.md</c>.
///
/// Lifecycle. <see cref="StartAsync"/> spawns the subprocess and starts a
/// background reader task draining stdout into the response /
/// notification dispatch path. <see cref="DisposeAsync"/> sends
/// <c>shutdown</c> and waits up to 5s before falling back to
/// <see cref="Process.Kill()"/>. <see cref="UnexpectedExit"/> fires when
/// the subprocess exits without a preceding shutdown request.
/// </remarks>
public sealed class EngineClient : IEngineClient
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull,
    };

    private readonly Func<SidecarCommand> _commandFactory;
    private readonly object _spawnLock = new();
    private Process? _process;
    private long _nextRequestId;
    private bool _shutdownRequested;
    private System.Text.StringBuilder? _stderrBuffer;

    // Pending one-shot responses keyed on request id.
    private readonly ConcurrentDictionary<long, TaskCompletionSource<JsonElement>> _pending = new();

    // Pending streaming requests: each yields ApplyEvents to a channel
    // writer until the final {"event":"done"} or an error.
    private readonly ConcurrentDictionary<long, ChannelWriter<ApplyEvent>> _streams = new();

    public event EventHandler<EngineExitedEventArgs>? UnexpectedExit;

    public EngineClient() : this(ResolveSidecarCommand) { }

    /// <summary>Test constructor allowing a fake spawn command.</summary>
    public EngineClient(Func<SidecarCommand> commandFactory)
    {
        _commandFactory = commandFactory;
    }

    public Task StartAsync(CancellationToken cancellationToken = default)
    {
        lock (_spawnLock)
        {
            if (_process is { HasExited: false })
            {
                return Task.CompletedTask;
            }
            _shutdownRequested = false;
            var cmd = _commandFactory();
            var psi = new ProcessStartInfo
            {
                FileName = cmd.FileName,
                Arguments = cmd.Arguments,
                WorkingDirectory = cmd.WorkingDirectory,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            foreach (var (k, v) in cmd.Environment)
            {
                psi.Environment[k] = v;
            }
            var process = new Process { StartInfo = psi, EnableRaisingEvents = true };
            process.Exited += OnProcessExited;
            if (!process.Start())
            {
                throw new InvalidOperationException(
                    $"Failed to spawn sidecar: {cmd.FileName} {cmd.Arguments}");
            }
            _process = process;
            _ = Task.Run(() => ReadStdoutLoopAsync(process), cancellationToken);
            // Drain stderr continuously into a buffer; otherwise the pipe
            // fills (~64 KB on Windows) and the sidecar blocks on its next
            // write to stderr. The buffer is read on OnProcessExited so the
            // unexpected-exit modal can include the daemon's last words.
            _stderrBuffer = new System.Text.StringBuilder();
            _ = Task.Run(() => DrainStderrLoopAsync(process), cancellationToken);
            return Task.CompletedTask;
        }
    }

    public async Task RestartAsync(CancellationToken cancellationToken = default)
    {
        await DisposeProcessAsync().ConfigureAwait(false);
        await StartAsync(cancellationToken).ConfigureAwait(false);
    }

    public Task<Settings> GetSettingsAsync(CancellationToken cancellationToken = default)
    {
        return CallAsync<Settings>("get_settings", new { }, cancellationToken);
    }

    public Task<Settings> SaveSettingsAsync(Settings settings, CancellationToken cancellationToken = default)
    {
        return CallAsync<Settings>("save_settings", new { settings }, cancellationToken);
    }

    public Task<ParseResolveResult> ParseAndResolveAsync(
        IReadOnlyList<string> paths,
        Settings? settings,
        CancellationToken cancellationToken = default)
    {
        return CallAsync<ParseResolveResult>(
            "parse_and_resolve",
            new { paths, settings },
            cancellationToken);
    }

    public Task<TmdbSearchResult> SearchTmdbFreeAsync(
        string query,
        string kind,
        Settings? settings,
        CancellationToken cancellationToken = default)
    {
        return CallAsync<TmdbSearchResult>(
            "search_tmdb_free",
            new { query, kind, settings },
            cancellationToken);
    }

    public Task<FindByImdbResult> FindByImdbAsync(
        string imdbId,
        ResolvedRow row,
        Settings? settings,
        CancellationToken cancellationToken = default)
    {
        return CallAsync<FindByImdbResult>(
            "find_by_imdb",
            new { imdb_id = imdbId, row, settings },
            cancellationToken);
    }

    public Task<AnchorSearchResult> IterateAnchorSearchAsync(
        string query,
        int? year,
        Settings? settings,
        CancellationToken cancellationToken = default)
    {
        return CallAsync<AnchorSearchResult>(
            "iterate_anchor_search",
            new { query, year, settings },
            cancellationToken);
    }

    public Task<SelectAnchorResult> SelectAnchorAsync(
        IReadOnlyList<ResolvedRow> rows,
        string groupKey,
        Candidate candidate,
        Settings? settings,
        CancellationToken cancellationToken = default)
    {
        return CallAsync<SelectAnchorResult>(
            "select_anchor",
            new { rows, group_key = groupKey, candidate, settings },
            cancellationToken);
    }

    public Task<EditRowResult> EditRowAsync(
        IReadOnlyList<ResolvedRow> rows,
        string rowId,
        EditRowOverrides overrides,
        Settings? settings,
        CancellationToken cancellationToken = default)
    {
        return CallAsync<EditRowResult>(
            "edit_row",
            new { rows, row_id = rowId, overrides, settings },
            cancellationToken);
    }

    public Task<BuildPlanResult> BuildPlanAsync(
        IReadOnlyList<ResolvedRow> rows,
        string? inputRoot,
        bool applyEditions,
        Settings? settings,
        CancellationToken cancellationToken = default)
    {
        return CallAsync<BuildPlanResult>(
            "build_plan",
            new { rows, input_root = inputRoot, apply_editions = applyEditions, settings },
            cancellationToken);
    }

    public Task<UndoReport> UndoBatchAsync(
        string journalPath,
        CancellationToken cancellationToken = default)
    {
        return CallAsync<UndoReport>(
            "undo_batch",
            new { journal_path = journalPath },
            cancellationToken);
    }

    public async IAsyncEnumerable<ApplyEvent> ApplyPlanAsync(
        PlanOp plan,
        bool cleanup,
        bool verifyHash,
        Settings? settings,
        [EnumeratorCancellation] CancellationToken cancellationToken = default)
    {
        // ChannelOptions: unbounded because the daemon emits progress at
        // FS-copy cadence (slow) and the consumer renders to the UI thread
        // (also slow). The total event count for one apply is plan.ops.length * 2 + 1
        // (one op_started + one op_verified per op + the final result), which is
        // bounded by the plan size; no risk of unbounded growth.
        var channel = Channel.CreateUnbounded<ApplyEvent>(new UnboundedChannelOptions
        {
            SingleReader = true,
            SingleWriter = true,
        });
        var id = Interlocked.Increment(ref _nextRequestId);
        _streams[id] = channel.Writer;
        var request = BuildRequest(id, "apply_plan", new { plan, cleanup, verify_hash = verifyHash, settings });
        await SendAsync(request, cancellationToken).ConfigureAwait(false);
        try
        {
            await foreach (var ev in channel.Reader.ReadAllAsync(cancellationToken))
            {
                yield return ev;
                if (ev.EventKind == "done")
                {
                    yield break;
                }
            }
        }
        finally
        {
            _streams.TryRemove(id, out _);
        }
    }

    public async ValueTask DisposeAsync()
    {
        await DisposeProcessAsync().ConfigureAwait(false);
        GC.SuppressFinalize(this);
    }

    private async Task DisposeProcessAsync()
    {
        Process? process;
        lock (_spawnLock)
        {
            process = _process;
            _process = null;
            _shutdownRequested = true;
        }
        if (process == null)
        {
            return;
        }
        process.Exited -= OnProcessExited;
        try
        {
            if (!process.HasExited)
            {
                // Best-effort graceful shutdown.
                var shutdownId = Interlocked.Increment(ref _nextRequestId);
                var shutdown = BuildRequest(shutdownId, "shutdown", null);
                try
                {
                    process.StandardInput.WriteLine(shutdown);
                    await process.StandardInput.FlushAsync().ConfigureAwait(false);
                }
                catch
                {
                    // Pipe may already be closed; fall through to wait + kill.
                }
                if (!process.WaitForExit(5_000))
                {
                    process.Kill();
                }
            }
        }
        finally
        {
            process.Dispose();
        }
    }

    private async Task<T> CallAsync<T>(string method, object? @params, CancellationToken cancellationToken)
        where T : notnull
    {
        var id = Interlocked.Increment(ref _nextRequestId);
        var tcs = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        _pending[id] = tcs;
        try
        {
            var request = BuildRequest(id, method, @params);
            await SendAsync(request, cancellationToken).ConfigureAwait(false);
            using var reg = cancellationToken.Register(() => tcs.TrySetCanceled(cancellationToken));
            var result = await tcs.Task.ConfigureAwait(false);
            var deserialized = result.Deserialize<T>(JsonOptions);
            if (deserialized is null)
            {
                throw new InvalidOperationException(
                    $"Daemon returned null result for {method}; expected {typeof(T).Name}.");
            }
            return deserialized;
        }
        finally
        {
            _pending.TryRemove(id, out _);
        }
    }

    private string BuildRequest(long id, string method, object? @params)
    {
        var envelope = new Dictionary<string, object?>
        {
            ["jsonrpc"] = "2.0",
            ["id"] = id,
            ["method"] = method,
        };
        if (@params != null)
        {
            envelope["params"] = @params;
        }
        return JsonSerializer.Serialize(envelope, JsonOptions);
    }

    private async Task SendAsync(string line, CancellationToken cancellationToken)
    {
        var process = _process ?? throw new InvalidOperationException(
            "EngineClient.SendAsync called before StartAsync. Spawn the sidecar first.");
        // Process.StandardInput is the .NET-side write half of the child's
        // stdin pipe. The child reads newline-delimited JSON, so we write
        // exactly one line per request.
        await process.StandardInput.WriteLineAsync(line.AsMemory(), cancellationToken).ConfigureAwait(false);
        await process.StandardInput.FlushAsync().ConfigureAwait(false);
    }

    private async Task ReadStdoutLoopAsync(Process process)
    {
        var reader = process.StandardOutput;
        try
        {
            while (!process.HasExited)
            {
                var line = await reader.ReadLineAsync().ConfigureAwait(false);
                if (line == null)
                {
                    // EOF — sidecar closed stdout.
                    break;
                }
                if (string.IsNullOrWhiteSpace(line))
                {
                    continue;
                }
                DispatchMessage(line);
            }
        }
        catch (Exception)
        {
            // Defensive: if the reader throws (pipe closed mid-read), fall
            // through to the exit handler so pending requests are failed.
        }
    }

    private async Task DrainStderrLoopAsync(Process process)
    {
        var reader = process.StandardError;
        try
        {
            while (!process.HasExited)
            {
                var line = await reader.ReadLineAsync().ConfigureAwait(false);
                if (line == null)
                {
                    break;
                }
                lock (_spawnLock)
                {
                    _stderrBuffer?.AppendLine(line);
                }
            }
        }
        catch (Exception)
        {
            // Reader closed; nothing useful to do.
        }
    }

    private void DispatchMessage(string line)
    {
        try
        {
            using var doc = JsonDocument.Parse(line);
            var root = doc.RootElement;

            // Notifications carry "method" + "params" but no "id" at the
            // top level. The request id (when the notification belongs to a
            // streaming request) lives inside params.id per the daemon's
            // protocol.
            if (root.TryGetProperty("method", out var methodEl) && methodEl.GetString() == "progress")
            {
                DispatchProgressNotification(root);
                return;
            }

            // Responses always carry "id" at the top level.
            if (!root.TryGetProperty("id", out var idEl) || idEl.ValueKind != JsonValueKind.Number)
            {
                return;
            }
            var id = idEl.GetInt64();

            // If this id is a streaming request awaiting its terminal
            // result, route the result through the channel as a "done"
            // event so the IAsyncEnumerable consumer sees it as the last
            // element. Otherwise route through the one-shot _pending map.
            if (_streams.TryGetValue(id, out var streamWriter))
            {
                if (root.TryGetProperty("error", out var errEl))
                {
                    streamWriter.TryWrite(new ApplyEvent
                    {
                        EventKind = "done",
                        Error = errEl.TryGetProperty("message", out var m) ? m.GetString() : null,
                    });
                }
                else if (root.TryGetProperty("result", out var resEl))
                {
                    var runReport = resEl.Deserialize<RunReport>(JsonOptions);
                    streamWriter.TryWrite(new ApplyEvent { EventKind = "done", Result = runReport });
                }
                streamWriter.TryComplete();
                return;
            }

            if (_pending.TryGetValue(id, out var tcs))
            {
                if (root.TryGetProperty("error", out var errEl))
                {
                    var message = errEl.TryGetProperty("message", out var m) ? m.GetString() : "(no message)";
                    tcs.TrySetException(new BridgeException(message ?? "(no message)"));
                }
                else if (root.TryGetProperty("result", out var resEl))
                {
                    tcs.TrySetResult(resEl.Clone());
                }
            }
        }
        catch (JsonException ex)
        {
            // Malformed line from the daemon — surface as failed pending
            // responses (we don't know which), so they don't hang forever.
            FailAllPending(new BridgeException($"Daemon emitted malformed JSON: {ex.Message}"));
        }
    }

    private void DispatchProgressNotification(JsonElement root)
    {
        if (!root.TryGetProperty("params", out var paramsEl))
        {
            return;
        }
        if (!paramsEl.TryGetProperty("id", out var idEl) || idEl.ValueKind != JsonValueKind.Number)
        {
            return;
        }
        var id = idEl.GetInt64();
        if (!_streams.TryGetValue(id, out var writer))
        {
            return;
        }
        var ev = new ApplyEvent
        {
            EventKind = paramsEl.TryGetProperty("event", out var evEl) ? (evEl.GetString() ?? "unknown") : "unknown",
            OpIndex = paramsEl.TryGetProperty("op_index", out var oi) && oi.ValueKind == JsonValueKind.Number
                ? oi.GetInt32() : null,
            TotalOps = paramsEl.TryGetProperty("total_ops", out var to) && to.ValueKind == JsonValueKind.Number
                ? to.GetInt32() : null,
            Source = paramsEl.TryGetProperty("source", out var src) ? src.GetString() : null,
            Target = paramsEl.TryGetProperty("target", out var tgt) ? tgt.GetString() : null,
            TotalBytes = paramsEl.TryGetProperty("total_bytes", out var tb) && tb.ValueKind == JsonValueKind.Number
                ? tb.GetInt64() : null,
            Bytes = paramsEl.TryGetProperty("bytes", out var b) && b.ValueKind == JsonValueKind.Number
                ? b.GetInt64() : null,
            Error = paramsEl.TryGetProperty("error", out var err) ? err.GetString() : null,
        };
        writer.TryWrite(ev);
    }

    private void OnProcessExited(object? sender, EventArgs e)
    {
        if (_shutdownRequested)
        {
            // Expected exit after our own shutdown request; quiet.
            FailAllPending(new BridgeException("Daemon shutdown."));
            return;
        }
        // Unexpected exit. Fail every in-flight request with a clear
        // error then fire the public event so the UI can render a
        // sidecar-died modal with a Restart button.
        var process = (Process)sender!;
        string? stderr;
        lock (_spawnLock)
        {
            stderr = _stderrBuffer?.ToString();
        }
        FailAllPending(new BridgeException($"Sidecar exited unexpectedly (code {process.ExitCode})."));
        UnexpectedExit?.Invoke(this, new EngineExitedEventArgs
        {
            ExitCode = process.ExitCode,
            Stderr = stderr,
        });
    }

    private void FailAllPending(Exception ex)
    {
        foreach (var kv in _pending)
        {
            kv.Value.TrySetException(ex);
        }
        _pending.Clear();
        foreach (var kv in _streams)
        {
            kv.Value.TryComplete(ex);
        }
        _streams.Clear();
    }

    /// <summary>
    /// Resolve the sidecar binary path for production runs. Looks for a
    /// sibling <c>plex-renamer-engined.exe</c> next to the WPF .exe
    /// first (installed mode); falls back to <c>uv run plex-renamer-engined</c>
    /// against <c>PLEX_RENAMER_REPO_ROOT</c> (dev mode).
    /// </summary>
    public static SidecarCommand ResolveSidecarCommand()
    {
        var exeDir = AppContext.BaseDirectory;
        var sibling = Path.Combine(exeDir, "plex-renamer-engined.exe");
        if (File.Exists(sibling))
        {
            return new SidecarCommand
            {
                FileName = sibling,
                Arguments = string.Empty,
                WorkingDirectory = exeDir,
            };
        }
        // Dev-mode fallback: spawn via uv from a configured repo root.
        var repoRoot = Environment.GetEnvironmentVariable("PLEX_RENAMER_REPO_ROOT");
        if (!string.IsNullOrEmpty(repoRoot) && Directory.Exists(repoRoot))
        {
            return new SidecarCommand
            {
                FileName = "uv",
                Arguments = "run --active plex-renamer-engined",
                WorkingDirectory = repoRoot,
            };
        }
        throw new FileNotFoundException(
            "Could not locate the sidecar binary. Expected either " +
            $"a sibling 'plex-renamer-engined.exe' next to '{exeDir}', " +
            "or PLEX_RENAMER_REPO_ROOT pointing at a checkout where " +
            "'uv run plex-renamer-engined' resolves. See windows-native/README.md.");
    }
}

public sealed class SidecarCommand
{
    public required string FileName { get; init; }
    public required string Arguments { get; init; }
    public required string WorkingDirectory { get; init; }
    public IReadOnlyDictionary<string, string> Environment { get; init; }
        = new Dictionary<string, string>();
}

public sealed class BridgeException : Exception
{
    public BridgeException(string message) : base(message) { }
}
