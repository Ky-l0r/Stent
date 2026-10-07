<#
.SYNOPSIS
    Stent 打包脚本：构建可独立运行的 Windows 目录包（onedir）。

.DESCRIPTION
    流程：检查环境 → 安装依赖 → 自检 → PyInstaller 打包 → 输出 dist\Stent\Stent.exe
    产物为目录包，双击 Stent.exe 即可运行（目标机器无需安装 Python）。

.PARAMETER SkipDeps
    跳过依赖安装（已装好时使用）。

.PARAMETER SkipTests
    跳过我方自检（不推荐）。

.PARAMETER InstallBrowser
    额外执行 playwright install chromium（把浏览器内核装到当前用户目录）。
    默认不执行：Stent 运行时复用系统已安装的 Chromium，缺失时会在界面提示。

.PARAMETER Clean
    打包前清理 build/ 与 dist/ 目录。

.EXAMPLE
    pwsh -File build.ps1 -Clean -InstallBrowser
#>
[CmdletBinding()]
param(
    [switch]$SkipDeps,
    [switch]$SkipTests,
    [switch]$InstallBrowser,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
Set-Location $Root

function Write-Step($text) {
    Write-Host ''
    Write-Host "==== $text ====" -ForegroundColor Cyan
}

function Invoke-Checked([string]$Exe, [string[]]$ArgList) {
    & $Exe @ArgList
    if ($LASTEXITCODE -ne 0) {
        throw "$Exe $($ArgList -join ' ') 执行失败（exit=$LASTEXITCODE）"
    }
}

Write-Step '1/6 检查 Python 环境'
$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) { throw '未找到 python，请先安装 Python 3.10+ 并加入 PATH' }
Invoke-Checked $python.Source @('-c', 'import sys; print("Python", sys.version.split()[0]); assert sys.version_info >= (3,10), "需要 Python 3.10+"')

Write-Step '2/6 安装依赖'
if ($SkipDeps) {
    Write-Host '已跳过（-SkipDeps）'
} else {
    Invoke-Checked $python.Source @('-m', 'pip', 'install', '--upgrade', '-r', 'requirements-dev.txt')
}

if ($InstallBrowser) {
    Write-Step '2.1 安装 Playwright Chromium'
    Invoke-Checked $python.Source @('-m', 'playwright', 'install', 'chromium')
}

Write-Step '3/6 运行服务层自检'
if ($SkipTests) {
    Write-Host '已跳过（-SkipTests）'
} else {
    Invoke-Checked $python.Source @('tests/selftest_all.py')
}

Write-Step '4/6 运行界面自检（离屏）'
if ($SkipTests) {
    Write-Host '已跳过（-SkipTests）'
} else {
    $env:QT_QPA_PLATFORM = 'offscreen'
    Invoke-Checked $python.Source @('main.py', '--selftest')
    Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
}

if ($Clean) {
    Write-Step '5/6 清理旧产物'
    foreach ($dir in 'build', 'dist') {
        if (Test-Path $dir) { Remove-Item $dir -Recurse -Force; Write-Host "已删除 $dir" }
    }
}

Write-Step '6/6 PyInstaller 打包（onedir）'
Invoke-Checked $python.Source @('-m', 'PyInstaller', 'Stent.spec', '--noconfirm', '--clean')

$exe = Join-Path $Root 'dist\Stent\Stent.exe'
if (-not (Test-Path $exe)) { throw "打包失败：未找到 $exe" }

$sizeMb = [math]::Round(((Get-ChildItem (Join-Path $Root 'dist\Stent') -Recurse -File |
    Measure-Object -Property Length -Sum).Sum / 1MB), 1)

Write-Host ''
Write-Host "打包完成：$exe" -ForegroundColor Green
Write-Host "产物体积：$sizeMb MB（企划书目标：< 200MB，不含浏览器内核）"
Write-Host ''
Write-Host '分发提示：'
Write-Host '  · 直接压缩 dist\Stent 目录发给用户，双击 Stent.exe 运行'
Write-Host '  · 目标机器首次使用「发布中心」需要 Playwright Chromium：'
Write-Host '    可在打包机上把 %USERPROFILE%\AppData\Local\ms-playwright 一并分发，'
Write-Host '    或让用户执行：python -m playwright install chromium'
