# plex-renamer (Windows native shell)

WPF / .NET 8 native Windows shell over the `plex-renamer-engined` JSON-RPC sidecar. This directory holds C# code; the Python engine lives at the repo root under `src/plex_renamer/`.

This slice (slice 2 of `plex-renamer-win-native`) stands up the scaffold: drop zone + two-panel layout + settings dialog + bridge client + WPF-UI Fluent theming + `[StaFact]` rendering-test pattern. The Apply button is rendered but disabled until the safety modals (collision review, cleanup confirm) land in a later slice.

## Dev setup

You need:

- Windows 10 / 11 (the project targets `net8.0-windows`).
- .NET 8 SDK on PATH.
- `uv` on PATH (for the dev-mode sidecar spawn).
- A clone of this repo with `uv sync` already run at the repo root (so the Python sidecar is importable).

Open `windows-native/PlexRenamer.sln` in Visual Studio 2022 (17.x) or Visual Studio Code with the C# Dev Kit, OR drive from the CLI:

```
cd windows-native
dotnet restore PlexRenamer.sln
dotnet build PlexRenamer.sln
```

## Run in dev

```
cd windows-native
$Env:PLEX_RENAMER_REPO_ROOT = "<absolute path to your plex-renamer clone>"
dotnet run --project PlexRenamer
```

`PLEX_RENAMER_REPO_ROOT` tells `EngineClient` where to find the Python source tree so it can spawn the sidecar via `uv run plex-renamer-engined`. See "Sidecar binary lookup" below for the full rule.

## Run tests

```
cd windows-native
dotnet test PlexRenamer.sln
```

The `RenderingTests` use xUnit.StaFact's `[StaFact]` attribute so each test runs on a single-threaded apartment with a real WPF AppDomain. Tests construct a control, call `Measure(Size.Infinite) + Arrange(rect)` to force layout, and assert non-zero `ActualWidth/ActualHeight` on the load-bearing children. This is the WPF analogue of the Qt `widget.grab() + sizeHint` discipline `INVARIANTS.md` mandates — the regression gate for the "squished / flaky" defect class the project exists to fix.

## Lint / format

```
cd windows-native
dotnet format PlexRenamer.sln --verify-no-changes
```

To auto-fix:

```
cd windows-native
dotnet format PlexRenamer.sln
```

## Sidecar binary lookup

`EngineClient.ResolveSidecarCommand()` picks the sidecar by this rule:

1. **Installed-mode**: if `plex-renamer-engined.exe` exists in the same directory as the running `PlexRenamer.exe`, spawn that binary directly with no arguments. This is the path the NSIS installer takes; both binaries live at `Program Files\plex-renamer\gui\`.
2. **Dev-mode**: if the sibling binary is missing, fall back to `uv run --active plex-renamer-engined` from the directory in the `PLEX_RENAMER_REPO_ROOT` env var. That env var must point at the Python source tree where `uv sync` has been run.
3. If neither lookup succeeds, `ResolveSidecarCommand` throws `FileNotFoundException` with a message naming both paths.

This makes the WPF .exe portable between the installed bundle and the dev source tree without runtime configuration.

## Bridge contract

The JSON-RPC protocol the bridge speaks is documented at `docs/win-native-bridge.md` (at the repo root). POCO records under `PlexRenamer.Bridge.Schemas` mirror that doc; if a POCO drifts from the doc, the doc wins — update the POCO.

## Apply button posture

Slice 2 renders the Apply button in the ActionBar but disables it (`IsEnabled="False"`) with a tooltip pointing at the future. Apply is wired and enabled once the collision-review / cleanup-confirm / run-report / undo dialogs land in a subsequent slice. Pressing Preview is fully wired in this slice — drop files, see source and target panels populate.

## Visual smoke testing

There is no automated pixel-diff testing in this slice (explicitly out of scope). To manually visually verify after edits, follow this checklist on a Windows 11 machine:

1. `dotnet run --project PlexRenamer` — verify the window opens with Mica backdrop (the slightly translucent material under the title bar; if you see a flat solid color, WPF-UI's theme isn't initializing).
2. Drag a folder of media files onto the drop zone — verify both source and target panels populate.
3. Click Settings — verify the dialog opens with Fluent control templates (rounded TextBox corners, Fluent CheckBox style).
4. Click Apply — verify the button is disabled and shows the "Collision review and cleanup confirmation arrive in a later step." tooltip on hover.
5. Resize the window to 900×600 (the minimum) and confirm no controls are clipped.

## Future slices

This shell is iteratively built. The action bar's Apply button + collision-review / cleanup-confirm modals + run report widget land in subsequent slices. The full per-row edit pane, show-anchor picker dialog, and confidence badge land before Apply is enabled. See the project brief at `~/.agent-coding/projects/plex-renamer-win-native/brief.md` for the full slice chain.
