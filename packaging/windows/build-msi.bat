@echo off
:: packaging/windows/build-msi.bat
::
:: Windows-native wrapper for the WiX MSI build. Equivalent to
:: scripts/build-msi.sh but uses cmd.exe so it can run on a stock
:: Windows host without a POSIX shell.
::
:: Usage (from repo root, after `make cross-windows`):
::   packaging\windows\build-msi.bat
::
:: Requires:
::   - WiX 3.x  (candle.exe + light.exe in PATH; install via wix-installer.exe)
::   - bin\nova.exe

setlocal enabledelayedexpansion

if not exist "%~dp0..\..\bin\nova.exe" (
    echo build-msi: ERROR: bin\nova.exe missing -- run 'make cross-windows' first
    exit /b 2
)

set /p VERSION=<"%~dp0..\..\VERSION"
set VERSION=%VERSION: =%
set MSI_BASENAME=nova-%VERSION%

where candle >nul 2>&1
if errorlevel 1 (
    echo build-msi: ERROR: candle.exe not found in PATH. Install WiX 3.x from https://wixtoolset.org/
    exit /b 2
)
where light >nul 2>&1
if errorlevel 1 (
    echo build-msi: ERROR: light.exe not found in PATH. Install WiX 3.x from https://wixtoolset.org/
    exit /b 2
)

if not exist "%~dp0..\..\dist" mkdir "%~dp0..\..\dist"

echo build-msi: running candle ...
candle ^
    -dSourceDir="%~dp0..\.." ^
    -dProductVersion=%VERSION% ^
    -arch x64 ^
    -o "%~dp0..\..\dist\nova.wixobj" ^
    "%~dp0nova.wxs"
if errorlevel 1 exit /b 3

echo build-msi: running light ...
light ^
    -ext WixUIExtension ^
    -ext WixUtilExtension ^
    -o "%~dp0..\..\dist\%MSI_BASENAME%.msi" ^
    "%~dp0..\..\dist\nova.wixobj"
if errorlevel 1 exit /b 3

del "%~dp0..\..\dist\nova.wixobj"

echo build-msi: built  : dist\%MSI_BASENAME%.msi
echo build-msi: done.
exit /b 0
