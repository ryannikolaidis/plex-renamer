using System.Threading.Tasks;
using PlexRenamer.Bridge;
using Xunit;

namespace PlexRenamer.Tests;

/// <summary>
/// Tests that exercise the bridge's failure-mode handling: unexpected
/// sidecar exit, malformed responses. The shell must surface these
/// cleanly without crashing or hanging in-flight requests.
/// </summary>
public class BridgeFailureTests
{
    [Fact]
    public void FakeEngineClient_RaiseUnexpectedExit_FiresEvent()
    {
        var fake = new FakeEngineClient();
        EngineExitedEventArgs? captured = null;
        fake.UnexpectedExit += (_, args) => captured = args;

        fake.RaiseUnexpectedExit(1, "the daemon died");

        Assert.NotNull(captured);
        Assert.Equal(1, captured!.ExitCode);
        Assert.Equal("the daemon died", captured.Stderr);
    }

    [Fact]
    public async Task FakeEngineClient_DisposeAsync_RecordsCall()
    {
        var fake = new FakeEngineClient();
        await fake.DisposeAsync();
        Assert.Contains("DisposeAsync", fake.CallsMade);
    }
}
