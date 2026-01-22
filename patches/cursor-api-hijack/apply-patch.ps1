# Cursor API Hijack Patch - Apply Script (Windows PowerShell)
#
# 将 hijack.js 补丁注入到 Cursor 的 workbench.desktop.main.js 文件中
#
# @version 1.0.0
# @author 浮浮酱 (Claude Opus 4.5)
# @date 2026-01-20

param (
    [string]$CursorPath = "C:\Program Files\cursor",
    [string]$GatewayUrl = "http://127.0.0.1:8181",
    [switch]$Force,
    [switch]$DryRun
)

# 严格模式
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 颜色输出函数
function Write-Success { param([string]$Message) Write-Host "[OK] $Message" -ForegroundColor Green }
function Write-Info { param([string]$Message) Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Warn { param([string]$Message) Write-Host "[WARN] $Message" -ForegroundColor Yellow }
function Write-Err { param([string]$Message) Write-Host "[ERROR] $Message" -ForegroundColor Red }

# 标题
Write-Host ""
Write-Host "========================================" -ForegroundColor Magenta
Write-Host "  Cursor API Hijack Patch - Installer  " -ForegroundColor Magenta
Write-Host "========================================" -ForegroundColor Magenta
Write-Host ""

# 检查管理员权限
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Err "需要管理员权限才能修改 Cursor 文件"
    Write-Info "请以管理员身份运行 PowerShell"
    exit 1
}

# 路径定义
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$patchFile = Join-Path $scriptDir "hijack.js"
$targetFile = Join-Path $CursorPath "resources\app\out\vs\workbench\workbench.desktop.main.js"
$backupDir = Join-Path $CursorPath "resources\app\out\vs\workbench\backups"

# 检查补丁文件是否存在
if (-not (Test-Path $patchFile)) {
    Write-Err "找不到补丁文件: $patchFile"
    exit 1
}

# 检查目标文件是否存在
if (-not (Test-Path $targetFile)) {
    Write-Err "找不到 Cursor 核心文件: $targetFile"
    Write-Info "请确认 Cursor 安装路径是否正确: $CursorPath"
    exit 1
}

Write-Info "补丁文件: $patchFile"
Write-Info "目标文件: $targetFile"
Write-Info "网关地址: $GatewayUrl"

# 读取补丁内容
$patchContent = Get-Content -Path $patchFile -Raw -Encoding UTF8

# 替换网关地址
if ($GatewayUrl -ne "http://127.0.0.1:8181") {
    Write-Info "更新网关地址..."
    $patchContent = $patchContent -replace "gatewayUrl: 'http://127.0.0.1:8181'", "gatewayUrl: '$GatewayUrl'"
}

# 读取目标文件
Write-Info "读取目标文件..."
$targetContent = Get-Content -Path $targetFile -Raw -Encoding UTF8

# 检查是否已经应用过补丁
$patchMarker = "__GCLI2API_HIJACK_VERSION"
if ($targetContent -match $patchMarker) {
    if ($Force) {
        Write-Warn "检测到已存在的补丁，使用 -Force 参数强制重新应用..."
        # 移除旧补丁（从开头到第一个原始代码之前）
        # 补丁以 IIFE 包裹，以 })(); 结束
        $targetContent = $targetContent -replace "^/\*\*[\s\S]*?\*\*\*/\(function\(\)[\s\S]*?\}\)\(\);", ""
    } else {
        Write-Warn "补丁已经应用过了"
        Write-Info "使用 -Force 参数可以强制重新应用"
        exit 0
    }
}

# 创建备份目录
if (-not (Test-Path $backupDir)) {
    Write-Info "创建备份目录..."
    New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
}

# 创建备份
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupFile = Join-Path $backupDir "workbench.desktop.main.js.backup.$timestamp"
Write-Info "创建备份: $backupFile"

if (-not $DryRun) {
    Copy-Item -Path $targetFile -Destination $backupFile -Force
    Write-Success "备份创建完成"
}

# 构建新内容（补丁在前，原始内容在后）
$newContent = @"
/**
 * GCLI2API Cursor Hijack Patch
 * Applied: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
 * Version: 1.0.0
 ***/
$patchContent
$targetContent
"@

# 写入文件
Write-Info "应用补丁..."
if (-not $DryRun) {
    # 使用 UTF-8 without BOM 编码
    $utf8NoBOM = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($targetFile, $newContent, $utf8NoBOM)
    Write-Success "补丁应用成功！"
} else {
    Write-Warn "[DryRun] 跳过实际写入操作"
}

# 验证
if (-not $DryRun) {
    $verifyContent = Get-Content -Path $targetFile -Raw -Encoding UTF8
    if ($verifyContent -match $patchMarker) {
        Write-Success "验证通过：补丁已正确应用"
    } else {
        Write-Err "验证失败：补丁可能未正确应用"
        Write-Info "请检查文件内容或使用备份恢复"
        exit 1
    }
}

# 提示
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "           补丁应用完成！              " -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Info "请重启 Cursor 使补丁生效"
Write-Info "备份文件位置: $backupFile"
Write-Host ""
Write-Info "验证方法："
Write-Host "  1. 打开 Cursor" -ForegroundColor White
Write-Host "  2. 打开开发者工具 (Ctrl+Shift+I)" -ForegroundColor White
Write-Host "  3. 在 Console 中输入: window.__GCLI2API_HIJACK_VERSION" -ForegroundColor White
Write-Host "  4. 应该显示版本号: 1.0.0" -ForegroundColor White
Write-Host ""
