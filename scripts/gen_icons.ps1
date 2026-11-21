Add-Type -AssemblyName System.Drawing

function Sweep([double]$start, [double]$end) {
    $s = $end - $start
    while ($s -lt 0) { $s += 360.0 }
    return $s
}

function Generate-Icon([int]$size, [string]$outPath, [bool]$maskable = $false) {
    $bmp = [System.Drawing.Bitmap]::new($size, $size)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

    # 1. 纯白底全幅铺满（直角正方形，不强加 Squircle SetClip，由系统根据平台规则自由裁切或加阴影，避免二次裁切放大失真）
    $g.Clear([System.Drawing.Color]::White)

    # 2. 安全区视口计算：
    # - maskable 图标（移动端自适应启动器）：内容收敛至中心 63% 安全区（外围 37% 留给圆形/水滴形/圆角裁切出血）
    # - 标准图标（桌面端 Windows/Mac/iOS PWA）：内容收敛至中心 78% 呼吸区（外围 22% 留白，居中端正不贴边）
    $contentRatio = if ($maskable) { 68.0 / 108.0 } else { 0.78 }
    $vSize = $size * $contentRatio
    $vOff = ($size - $vSize) / 2.0
    $scale = $vSize / 512.0

    function VX([double]$x) { return [float]($vOff + $x * $scale) }
    function VY([double]$y) { return [float]($vOff + $y * $scale) }

    # 3. 底部品牌蓝底山峰曲线：横贯 0..size 贴满底边出血
    $blueColor = [System.Drawing.ColorTranslator]::FromHtml("#2278f8")
    $blueBrush = [System.Drawing.SolidBrush]::new($blueColor)

    $apexY = VY 336.0
    $edgeY = VY 392.0
    $hillPath = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $hillPath.AddLine(0.0, [float]$size, [float]$size, [float]$size)
    $hillPath.AddLine([float]$size, [float]$size, [float]$size, $edgeY)
    $hillPath.AddBezier(
        [float]$size, $edgeY,
        (VX 400.0), $apexY,
        (VX 112.0), $apexY,
        0.0, $edgeY
    )
    $hillPath.CloseFigure()
    $g.FillPath($blueBrush, $hillPath)

    # 4. 115 经典官方云朵几何造型（稳稳坐落于安全区上半部）
    $cloudScale = 1.72 * $scale
    $cloudCenterY = VY 176.0

    function CX([double]$x) { return [float](($size / 2.0) + ($x - 127.5) * $cloudScale) }
    function CY([double]$y) { return [float]($cloudCenterY + ($y - 107.0) * $cloudScale) }

    $cPath = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $rL = 38.0 * $cloudScale
    $rR = 38.0 * $cloudScale
    $rT = 42.0 * $cloudScale

    # 4.1 右侧内卷与右半圆（顺时针从 180 度扫 270 度至 90 度底部）
    $cPath.AddArc((CX 165.5) - [float]$rR, (CY 127.0) - [float]$rR, [float]($rR * 2), [float]($rR * 2), 180.0, 270.0)

    # 4.2 底部水平平切线
    $cPath.AddLine((CX 165.5), (CY 165.0), (CX 89.5), (CY 165.0))

    # 4.3 左半圆（90 度顺时针至相交角 264.35 度）
    $swL = Sweep 90.0 264.35
    $cPath.AddArc((CX 89.5) - [float]$rL, (CY 127.0) - [float]$rL, [float]($rL * 2), [float]($rL * 2), 90.0, [float]$swL)

    # 4.4 顶部主穹顶大半圆（173.6 度顺时针至相交角 6.4 度）
    $swT = Sweep 173.6 6.4
    $cPath.AddArc((CX 127.5) - [float]$rT, (CY 84.5) - [float]$rT, [float]($rT * 2), [float]($rT * 2), 173.6, [float]$swT)

    $strokeW = [float](17.0 * $cloudScale)
    $cloudPen = [System.Drawing.Pen]::new($blueColor, $strokeW)
    $cloudPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $cloudPen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $cloudPen.LineJoin = [System.Drawing.Drawing2D.LineJoin]::Round
    $g.DrawPath($cloudPen, $cPath)

    # 5. 下半部文本：“115转存助手”（居中稳居于安全区下半部，绝不被任何桌面裁切切断）
    $fontSize = [float](41.0 * $scale)
    $font = [System.Drawing.Font]::new("Microsoft YaHei", $fontSize, [System.Drawing.FontStyle]::Bold)
    $sf = [System.Drawing.StringFormat]::new()
    $sf.Alignment = [System.Drawing.StringAlignment]::Center
    $sf.LineAlignment = [System.Drawing.StringAlignment]::Center
    $whiteBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
    $g.DrawString("115转存助手", $font, $whiteBrush, [float]($size / 2.0), (VY 424.0), $sf)

    # 6. 非 maskable 图标加一层 1px 微妙描边（防止在极少数纯白桌面背景下边缘无法分辨）
    if (-not $maskable) {
        $borderPen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(30, 0, 0, 0), 1.0)
        $g.DrawRectangle($borderPen, 0, 0, $size - 1, $size - 1)
    }

    $g.Dispose()
    $bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    Write-Output "Generated $outPath ($size x $size)"
}

# 1. 生成 Web PWA 与 iOS 主屏全套图标
Generate-Icon 192 "web\public\icon-192.png" $false
Generate-Icon 512 "web\public\icon-512.png" $false
Generate-Icon 512 "web\public\icon-maskable-512.png" $true
Generate-Icon 180 "web\public\apple-touch-icon.png" $false

# 2. 同步覆盖到 backend\static 预构建静态目录
$staticDir = "backend\static"
if (Test-Path $staticDir) {
    Copy-Item "web\public\icon-192.png" "$staticDir\icon-192.png" -Force
    Copy-Item "web\public\icon-512.png" "$staticDir\icon-512.png" -Force
    Copy-Item "web\public\icon-maskable-512.png" "$staticDir\icon-maskable-512.png" -Force
    Copy-Item "web\public\apple-touch-icon.png" "$staticDir\apple-touch-icon.png" -Force
    Write-Output "Synced PWA icons to $staticDir"
}

# 3. 同步到 Android 启动图标资源
$androidDrawable = "android\app\src\main\res\drawable-nodpi"
if (-not (Test-Path $androidDrawable)) { New-Item -ItemType Directory -Path $androidDrawable -Force | Out-Null }
Copy-Item "web\public\icon-512.png" "$androidDrawable\ic_app.png" -Force
Generate-Icon 512 "$androidDrawable\ic_app_foreground.png" $true
Write-Output "Synced Android launcher icons to $androidDrawable"
