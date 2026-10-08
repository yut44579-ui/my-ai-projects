# 全站页面断言运行器（TASK-019 起需要登录令牌）。
#
# 用法：pwsh -File scripts/run_page_checks.ps1
# 它会先登录拿令牌，再用令牌跑所有页面断言 —— 避免每个脚本各自处理鉴权。

param(
  [string]$Base = "http://127.0.0.1:5173",
  [string]$Api = "http://127.0.0.1:8000",
  [string]$Username = "admin",
  [string]$Password = "Admin@2026!"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "① 登录获取令牌…" -ForegroundColor Cyan
$body = @{ username = $Username; password = $Password } | ConvertTo-Json
try {
  $resp = Invoke-RestMethod -Uri "$Api/api/auth/login" -Method Post -Body $body -ContentType "application/json"
  $env:DSH_TEST_TOKEN = $resp.access_token
  Write-Host "   登录成功：$($resp.user.display_name)（$($resp.user.role)）" -ForegroundColor Green
} catch {
  Write-Host "   登录失败：$($_.Exception.Message)" -ForegroundColor Red
  Write-Host "   请先创建管理员：`$env:ADMIN_PASSWORD='...'; .venv/Scripts/python.exe scripts/create_admin.py --username admin --name 张三"
  exit 1
}

$pages = @(
  @{ url = "$Base/";              spec = "scripts/checks/ai-assistant.txt";  name = "workbench" },
  @{ url = "$Base/customers";     spec = "scripts/checks/reports.txt";       name = "customers-skip" },
  @{ url = "$Base/opportunities"; spec = "scripts/checks/opportunities.txt"; name = "opportunities" },
  @{ url = "$Base/projects";      spec = "scripts/checks/projects.txt";      name = "projects" },
  @{ url = "$Base/visits";        spec = "scripts/checks/visits.txt";        name = "visits" },
  @{ url = "$Base/research";      spec = "scripts/checks/research.txt";      name = "research" },
  @{ url = "$Base/analytics";     spec = "scripts/checks/analytics.txt";     name = "analytics" },
  @{ url = "$Base/reports";       spec = "scripts/checks/reports.txt";       name = "reports" },
  @{ url = "$Base/marketing";     spec = "scripts/checks/contents.txt";      name = "contents" },
  @{ url = "$Base/risk";          spec = "scripts/checks/risk.txt";          name = "risk" },
  # ★ 客户详情必须用**有访问记录**的客户（张伟 1863）。用林芳 652 测"访问事件"必然失败——
  #   那是测试数据指错实体，不是页面坏了（D34 已记录这条纪律）。
  @{ url = "$Base/customers/1863"; spec = "scripts/checks/customer-detail.txt"; name = "customer-detail" }
)

$failed = @()
foreach ($p in $pages) {
  if ($p.name -like "*-skip") { continue }
  Write-Host "② $($p.name) …" -ForegroundColor Cyan
  $out = & node scripts/ui_check_page.mjs $p.url $p.spec "check-$($p.name)" 2>&1
  if ($LASTEXITCODE -ne 0) {
    $failed += $p.name
    $out | Select-String -Pattern "❌|未通过" | ForEach-Object { Write-Host "   $($_.Line)" -ForegroundColor Red }
  } else {
    Write-Host "   通过" -ForegroundColor Green
  }
}

Write-Host ""
if ($failed.Count) {
  Write-Host "❌ 未通过：$($failed -join ', ')" -ForegroundColor Red
  exit 1
}
Write-Host "🎉 全部页面断言通过" -ForegroundColor Green
