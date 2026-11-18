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

    $scale = $size / 512.0

    # 1. 基础圆角裁剪（非 maskable 裁剪为优雅 Squircle，maskable 全量铺满底板用于自适应安全区）
    $radius = [int](112 * $scale)
    $sqPath = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $d = $radius * 2
    $sqPath.AddArc(0, 0, $d, $d, 180, 90)
    $sqPath.AddArc($size - $d, 0, $d, $d, 270, 90)
    $sqPath.AddArc($size - $d, $size - $d, $d, $d, 0, 90)
    $sqPath.AddArc(0, $size - $d, $d, $d, 90, 90)
    $sqPath.CloseFigure()

    if (-not $maskable) {
        $g.SetClip($sqPath)
    }

    # 2. 纯白背景
    $g.Clear([System.Drawing.Color]::White)

    $contentScale = if ($maskable) { 0.84 } else { 1.0 }

    # 3. 底部品牌蓝底山峰曲线（适度上移至 apex=338，贴合红线指引）
    $baseApexY = if ($maskable) { 348.0 } else { 338.0 }
    $baseEdgeY = $baseApexY + 50.0

    function MapHillY([double]$y) {
        $mid = 256.0
        $scaledY = $mid + ($y - $mid) * $contentScale
        return [float]($scaledY * $scale)
    }

    $hillPath = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $hillPath.AddLine(0.0, [float]$size, [float]$size, [float]$size)
    $hillPath.AddLine([float]$size, [float]$size, [float]$size, (MapHillY $baseEdgeY))
    $hillPath.AddBezier(
        [float]$size, (MapHillY $baseEdgeY),
        [float](360.0 * $scale), (MapHillY $baseApexY),
        [float](152.0 * $scale), (MapHillY $baseApexY),
        0.0, (MapHillY $baseEdgeY)
    )
    $hillPath.CloseFigure()

    $blueColor = [System.Drawing.ColorTranslator]::FromHtml("#2278f8")
    $blueBrush = [System.Drawing.SolidBrush]::new($blueColor)
    $g.FillPath($blueBrush, $hillPath)

    # 4. 115 经典官方云朵几何造型
    $cloudScale = 1.78 * $scale * $contentScale
    $baseCY = if ($maskable) { 180.0 } else { 170.0 }
    $cloudCenterY = $baseCY * $scale

    function CX([double]$x) {
        return [float](($size / 2.0) + ($x - 127.5) * $cloudScale)
    }
    function CY([double]$y) {
        return [float]($cloudCenterY + ($y - 107.0) * $cloudScale)
    }

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

    # 5. 下半部文本：“115转存助手” —— 保持绝对物理位置不变（Y=430）
    $fontSize = [float](44.0 * $scale * $contentScale)
    $font = [System.Drawing.Font]::new("Microsoft YaHei", $fontSize, [System.Drawing.FontStyle]::Bold)
    $sf = [System.Drawing.StringFormat]::new()
    $sf.Alignment = [System.Drawing.StringAlignment]::Center
    $sf.LineAlignment = [System.Drawing.StringAlignment]::Center
    $whiteBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)

    $baseTextY = if ($maskable) { 420.0 } else { 430.0 }
    $textY = [float]($baseTextY * $scale)
    $g.DrawString("115转存助手", $font, $whiteBrush, [float]($size / 2.0), $textY, $sf)

    # 6. 外层浅色边缘勾勒（全白桌面背景下辨识度）
    if (-not $maskable) {
        $borderPen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(45, 0, 0, 0), [float](2.0 * $scale))
        $g.ResetClip()
        $g.DrawPath($borderPen, $sqPath)
    }

    $g.Dispose()
    $bmp.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    Write-Output "Generated $outPath ($size x $size)"
}

Generate-Icon 192 "web\public\icon-192.png" $false
Generate-Icon 512 "web\public\icon-512.png" $false
Generate-Icon 512 "web\public\icon-maskable-512.png" $true
Generate-Icon 180 "web\public\apple-touch-icon.png" $false

# 同步到 Android 启动图标资源，确保全端图标完全一致
$androidDrawable = "android\app\src\main\res\drawable-nodpi"
if (-not (Test-Path $androidDrawable)) { New-Item -ItemType Directory -Path $androidDrawable -Force | Out-Null }
Copy-Item "web\public\icon-512.png" "$androidDrawable\ic_app.png" -Force
Copy-Item "web\public\icon-maskable-512.png" "$androidDrawable\ic_app_foreground.png" -Force
Write-Output "Synced Android launcher icons to $androidDrawable"
