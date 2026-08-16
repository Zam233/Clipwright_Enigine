# 停止开发实例（按端口 8080/5173/8090 结束进程）
foreach ($p in 8080, 5173, 8090) {
    $conns = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        Write-Host "已停止端口 $p (PID $($c.OwningProcess))" -ForegroundColor Green
    }
}
Write-Host '完成'
