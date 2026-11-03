@echo off
rem CeeSafe.Web M4 interop on Windows (docs/milestones/M4.md): builds examples/https and runs Go's crypto/tls client
rem against it. Needs ceesafec, LLVM with Visual Studio's C++ build tools, Go, and OpenSSL (Shining Light's
rem Win64ARM or Win64 installer). Set OPENSSL_LIB_DIR below for an x64 machine. testssl.sh needs hexdump, which Git
rem Bash lacks: run it from another machine through `ssh -L 18450:127.0.0.1:18443`.
set "OPENSSL_DIR=C:\Program Files\OpenSSL-Win64-ARM"
set "OPENSSL_LIB_DIR=C:\Program Files\OpenSSL-Win64-ARM\lib\VC\arm64\MD"
set "PATH=%USERPROFILE%\.ceesafe\bin;C:\Program Files\OpenSSL-Win64-ARM\bin;C:\Program Files\LLVM\bin;C:\Program Files\Go\bin;%PATH%"
set "ROOT=%USERPROFILE%\dev\CeeSafe.Web"
set "CERTS=%ROOT%\packages\web_tls\tests\certs"
cd /d "%ROOT%\examples\https" || exit /b 1
ceesafec fetch >nul && ceesafec build >"%TEMP%\cw-build.log" 2>&1 || (type "%TEMP%\cw-build.log" & exit /b 1)
echo == built examples/https
set CW_PORT=18443
set "CW_CERT=%CERTS%\server.pem"
set "CW_KEY=%CERTS%\server.key"
start "cw-https" /b out\https.exe >"%TEMP%\cw-https.log" 2>&1
ping -n 3 127.0.0.1 >nul
echo == go client
cd /d "%ROOT%\tests\interop\go"
go run . client 18443 "%CERTS%\ca.pem"
taskkill /f /im https.exe >nul 2>&1
echo == done
