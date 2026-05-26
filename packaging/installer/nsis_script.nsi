; NSIS installer script for plex-renamer Windows release.
;
; Driven from .github/workflows/release.yml and `make build-win`. The
; build produces three artifact directories under ``dist/``:
;
;   dist/plex-renamer-cli/        (PyInstaller; contains plex-renamer.exe + DLLs)
;   dist/plex-renamer-engined/    (PyInstaller; sidecar daemon binary)
;   dist/plex-renamer-gui/        (dotnet publish; WPF .NET 8 native shell)
;
; This script packages all three into a single setup .exe. The WPF .exe
; (``PlexRenamer.exe``) and the sidecar binary
; (``plex-renamer-engined.exe``) install side-by-side under ``gui\`` so
; the WPF EngineClient finds the sidecar via the sibling-path lookup
; rule documented in ``windows-native/README.md``.
;
; The legacy Qt GUI .exe no longer ships on Windows. The installer is
; unsigned for the first release; signing is a follow-up.

!define APP_NAME "plex-renamer"
; APP_VERSION is normally passed in via ``makensis -DAPP_VERSION=<version>``
; from the release workflow, which sources the version from
; ``packaging/pyinstaller_spec.app_version()`` so it stays in lockstep
; with ``pyproject.toml``. The fallback is here so ad-hoc local
; invocations (``makensis packaging/installer/nsis_script.nsi`` with no
; -D flag) still succeed; the installed registry version then reads
; ``0.0.0+dev`` to make the drift obvious.
!ifndef APP_VERSION
    !define APP_VERSION "0.0.0+dev"
!endif
!define APP_PUBLISHER "Ryan Nikolaidis"
!define APP_INSTALL_DIR "$PROGRAMFILES64\${APP_NAME}"

Name "${APP_NAME}"
; OutFile resolves relative to the script's directory, same as File.
; Walk two parents up to the repo root's dist/ where every other
; artifact lives.
OutFile "..\..\dist\plex-renamer-setup.exe"
InstallDir "${APP_INSTALL_DIR}"
RequestExecutionLevel admin
ShowInstDetails show
ShowUninstDetails show
SetCompressor /SOLID lzma

Page directory
Page instfiles

UninstPage uninstConfirm
UninstPage instfiles

Section "Install"
    SetOutPath "$INSTDIR"

    ; CLI bundle: one-folder PyInstaller 6.x dist with the .exe at the
    ; top and an _internal\ directory holding the runtime. NSIS resolves
    ; File paths relative to the SCRIPT'S directory (packaging\installer\),
    ; not the makensis invocation directory, so the dist/ tree at the
    ; repo root is two parents up. List the .exe + the _internal folder
    ; explicitly (the bare * glob misses _internal\ on PyInstaller 6
    ; layouts; *.* misses the dot-less directory too).
    SetOutPath "$INSTDIR\cli"
    File "..\..\dist\plex-renamer-cli\plex-renamer.exe"
    File /r "..\..\dist\plex-renamer-cli\_internal"

    ; GUI bundle: the WPF .NET 8 native shell (PlexRenamer.exe) AND the
    ; engine sidecar binary (plex-renamer-engined.exe) install side-by-
    ; side. EngineClient.ResolveSidecarCommand looks for the sidecar as
    ; a sibling of the WPF .exe (installed-mode path), so placing both
    ; under the same gui\ directory satisfies that contract without any
    ; runtime env vars. ``dotnet publish`` writes a flat dist directory
    ; with PlexRenamer.exe + its dependent assemblies; the /r File glob
    ; copies the whole layout.
    SetOutPath "$INSTDIR\gui"
    File /r "..\..\dist\plex-renamer-gui\*"
    ; The sidecar comes from a separate PyInstaller bundle; copy its
    ; .exe + _internal/ alongside the WPF assemblies.
    File "..\..\dist\plex-renamer-engined\plex-renamer-engined.exe"
    File /r "..\..\dist\plex-renamer-engined\_internal"

    ; Start-menu shortcut to the WPF GUI.
    CreateDirectory "$SMPROGRAMS\${APP_NAME}"
    CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\gui\PlexRenamer.exe"

    ; Uninstaller.
    WriteUninstaller "$INSTDIR\uninstall.exe"

    ; Add/Remove Programs registry entry.
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
        "DisplayName" "${APP_NAME}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
        "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
        "Publisher" "${APP_PUBLISHER}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
        "UninstallString" "$INSTDIR\uninstall.exe"
SectionEnd

Section "Uninstall"
    Delete "$INSTDIR\uninstall.exe"
    RMDir /r "$INSTDIR\cli"
    RMDir /r "$INSTDIR\gui"
    RMDir "$INSTDIR"
    Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
    RMDir "$SMPROGRAMS\${APP_NAME}"
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
SectionEnd
