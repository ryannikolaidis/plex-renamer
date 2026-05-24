; NSIS installer script for plex-renamer Windows release.
;
; Driven from .github/workflows/release.yml and `make build-win`. The
; PyInstaller spec at packaging/windows/plex-renamer.spec produces two
; one-folder bundles under ``dist/``:
;
;   dist/plex-renamer-cli/   (contains plex-renamer.exe + DLLs)
;   dist/plex-renamer-gui/   (contains plex-renamer-gui.exe + Qt DLLs)
;
; This script packages BOTH folders into a single setup .exe that
; installs them under ``%ProgramFiles%\plex-renamer``. The installer is
; unsigned for the first release; signing is a follow-up.

!define APP_NAME "plex-renamer"
!define APP_VERSION "0.1.0"
!define APP_PUBLISHER "Ryan Nikolaidis"
!define APP_INSTALL_DIR "$PROGRAMFILES64\${APP_NAME}"

Name "${APP_NAME}"
OutFile "dist\plex-renamer-setup.exe"
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

    ; CLI bundle: one-folder PyInstaller dist.
    SetOutPath "$INSTDIR\cli"
    File /r "dist\plex-renamer-cli\*"

    ; GUI bundle: one-folder PyInstaller dist.
    SetOutPath "$INSTDIR\gui"
    File /r "dist\plex-renamer-gui\*"

    ; Start-menu shortcut to the GUI.
    CreateDirectory "$SMPROGRAMS\${APP_NAME}"
    CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\gui\plex-renamer-gui.exe"

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
