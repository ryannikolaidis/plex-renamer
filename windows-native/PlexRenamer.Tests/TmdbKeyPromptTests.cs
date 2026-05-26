using PlexRenamer.Views;
using Xunit;

namespace PlexRenamer.Tests;

public class TmdbKeyPromptTests
{
    [StaFact]
    public void TmdbKeyPrompt_Constructs_WithEmptyEnteredKey()
    {
        var prompt = new TmdbKeyPrompt();
        // The Window's Content (a Grid) lays out correctly when the
        // dialog is constructed. Direct ActualWidth/Height on a
        // FluentWindow without showing it stays 0 — exercise the inner
        // content instead (the WPF analogue of the Qt sizeHint
        // discipline for dialogs).
        Assert.Equal(480, prompt.Width);
        Assert.Equal(280, prompt.Height);
        Assert.Null(prompt.EnteredKey);
    }
}
