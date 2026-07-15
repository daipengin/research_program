param(
    [string]$ResultsDir = "results/n_sweep_v3"
)

$ErrorActionPreference = "Stop"
$culture = [System.Globalization.CultureInfo]::InvariantCulture
$rows = Import-Csv -LiteralPath (Join-Path $ResultsDir "condition_metrics.csv")
$figureDir = Join-Path $ResultsDir "figures"
New-Item -ItemType Directory -Force -Path $figureDir | Out-Null

function F([double]$value) { $value.ToString("0.###", $culture) }
function XLog([double]$k, [double]$left, [double]$width) {
    $left + $width * ([Math]::Log10($k) / [Math]::Log10(2000.0))
}
function YLinear([double]$value, [double]$maximum, [double]$top, [double]$height) {
    $top + $height * (1.0 - [Math]::Min([Math]::Max($value, 0.0), $maximum) / $maximum)
}
function AddText($svg, [double]$x, [double]$y, [string]$text, [int]$size = 13, [string]$anchor = "start", [string]$weight = "normal") {
    $escaped = [System.Security.SecurityElement]::Escape($text)
    [void]$svg.AppendLine("<text x='$(F $x)' y='$(F $y)' font-size='$size' text-anchor='$anchor' font-weight='$weight' fill='#202124'>$escaped</text>")
}

$colors = @{ 5 = "#0072B2"; 10 = "#009E73"; 20 = "#E69F00"; 50 = "#D55E00" }
$series = foreach ($functionName in @("KURAMOTO", "LINEAR")) {
    foreach ($n in @(5, 10, 20, 50)) {
        [pscustomobject]@{
            Function = $functionName
            N = $n
            Rows = @($rows | Where-Object { $_.coupling_function -eq $functionName -and [int]$_.device_count -eq $n } | Sort-Object { [double]$_.k })
        }
    }
}

$panels = @(
    @{ Title = "Median PER (%)"; Column = "overall_per_percent_median"; Maximum = [Math]::Max(1.0, (($rows | ForEach-Object { [double]$_.overall_per_percent_median } | Measure-Object -Maximum).Maximum)); X = 80; Y = 70 },
    @{ Title = "Max-gap convergence rate (%)"; Column = "max_convergence_rate_percent"; Maximum = 100.0; X = 740; Y = 70 },
    @{ Title = "Intended min-gap convergence rate (%)"; Column = "mingap_convergence_rate_percent"; Maximum = 100.0; X = 80; Y = 570 },
    @{ Title = "Max-gap censored median cycle"; Column = "max_convergence_cycle_censored_median"; Maximum = 180.0; X = 740; Y = 570 }
)

$svg = [System.Text.StringBuilder]::new()
[void]$svg.AppendLine("<svg xmlns='http://www.w3.org/2000/svg' width='1400' height='1100' viewBox='0 0 1400 1100' font-family='Segoe UI,Arial,sans-serif'>")
[void]$svg.AppendLine("<rect width='1400' height='1100' fill='white'/>")
AddText $svg 700 32 "n_sweep_v3 performance overview (1000 runs per condition)" 21 "middle" "bold"

foreach ($panel in $panels) {
    $left = [double]$panel.X; $top = [double]$panel.Y; $width = 560.0; $height = 390.0
    [void]$svg.AppendLine("<rect x='$(F $left)' y='$(F $top)' width='$(F $width)' height='$(F $height)' fill='#fafafa' stroke='#b8b8b8'/>")
    AddText $svg ($left + $width / 2) ($top - 15) $panel.Title 16 "middle" "bold"
    foreach ($tick in @(1, 10, 100, 1000, 2000)) {
        $x = XLog $tick $left $width
        [void]$svg.AppendLine("<line x1='$(F $x)' y1='$(F $top)' x2='$(F $x)' y2='$(F ($top+$height))' stroke='#e2e2e2'/>")
        AddText $svg $x ($top + $height + 20) ([string]$tick) 11 "middle"
    }
    foreach ($fraction in @(0.0, 0.25, 0.5, 0.75, 1.0)) {
        $value = [double]$panel.Maximum * $fraction
        $y = YLinear $value ([double]$panel.Maximum) $top $height
        [void]$svg.AppendLine("<line x1='$(F $left)' y1='$(F $y)' x2='$(F ($left+$width))' y2='$(F $y)' stroke='#e2e2e2'/>")
        AddText $svg ($left - 8) ($y + 4) ($value.ToString("0.##", $culture)) 11 "end"
    }
    AddText $svg ($left + $width / 2) ($top + $height + 42) "K (log scale)" 12 "middle"
    foreach ($item in $series) {
        $points = [System.Collections.Generic.List[string]]::new()
        foreach ($row in $item.Rows) {
            $raw = $row.($panel.Column)
            if ($null -eq $raw -or $raw -eq "") { continue }
            $x = XLog ([double]$row.k) $left $width
            $y = YLinear ([double]$raw) ([double]$panel.Maximum) $top $height
            $points.Add("$(F $x),$(F $y)")
        }
        if ($points.Count -gt 0) {
            $dash = if ($item.Function -eq "LINEAR") { " stroke-dasharray='7 5'" } else { "" }
            [void]$svg.AppendLine("<polyline points='$($points -join ' ')' fill='none' stroke='$($colors[$item.N])' stroke-width='2'$dash/>")
        }
    }
}

$legendX = 470.0; $legendY = 1045.0
foreach ($index in 0..7) {
    $item = $series[$index]
    $x = $legendX + ($index % 4) * 135
    $y = $legendY + [Math]::Floor($index / 4) * 22
    $dash = if ($item.Function -eq "LINEAR") { " stroke-dasharray='7 5'" } else { "" }
    [void]$svg.AppendLine("<line x1='$(F $x)' y1='$(F $y)' x2='$(F ($x+28))' y2='$(F $y)' stroke='$($colors[$item.N])' stroke-width='3'$dash/>")
    AddText $svg ($x + 34) ($y + 4) "$($item.Function) N=$($item.N)" 11
}
[void]$svg.AppendLine("</svg>")
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$performancePath = Join-Path $figureDir "performance_overview.svg"
[System.IO.File]::WriteAllText(
    $performancePath,
    $svg.ToString().Replace("`r`n", "`n").TrimEnd("`r", "`n") + "`n",
    $utf8NoBom
)

$bandSvg = [System.Text.StringBuilder]::new()
[void]$bandSvg.AppendLine("<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='720' viewBox='0 0 1200 720' font-family='Segoe UI,Arial,sans-serif'>")
[void]$bandSvg.AppendLine("<rect width='1200' height='720' fill='white'/>")
AddText $bandSvg 600 38 "K bands with convergence rate >= 95%" 21 "middle" "bold"
$left = 235.0; $width = 880.0; $top = 85.0
foreach ($tick in @(1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000)) {
    $x = XLog $tick $left $width
    [void]$bandSvg.AppendLine("<line x1='$(F $x)' y1='65' x2='$(F $x)' y2='650' stroke='#e5e5e5'/>")
    AddText $bandSvg $x 680 ([string]$tick) 11 "middle"
}
for ($index = 0; $index -lt $series.Count; $index++) {
    $item = $series[$index]; $sample = $item.Rows[0]; $y = $top + $index * 70
    AddText $bandSvg 215 ($y + 5) "$($item.Function)  N=$($item.N)" 13 "end" "bold"
    [void]$bandSvg.AppendLine("<line x1='$(F $left)' y1='$(F ($y-10))' x2='$(F ($left+$width))' y2='$(F ($y-10))' stroke='#dddddd'/>")
    foreach ($kind in @(
        @{ Lower = "max_safe_k_ge95_lower"; Upper = "max_safe_k_ge95_upper"; Offset = -12; Color = "#6A3D9A"; Label = "max" },
        @{ Lower = "mingap_safe_k_ge95_lower"; Upper = "mingap_safe_k_ge95_upper"; Offset = 12; Color = "#1B9E77"; Label = "min-gap" }
    )) {
        $low = $sample.($kind.Lower); $high = $sample.($kind.Upper)
        if ($low -ne "" -and $high -ne "") {
            $x1 = XLog ([double]$low) $left $width; $x2 = XLog ([double]$high) $left $width
            [void]$bandSvg.AppendLine("<line x1='$(F $x1)' y1='$(F ($y+$kind.Offset))' x2='$(F $x2)' y2='$(F ($y+$kind.Offset))' stroke='$($kind.Color)' stroke-width='10' stroke-linecap='round'/>")
        }
    }
}
[void]$bandSvg.AppendLine("<line x1='430' y1='705' x2='470' y2='705' stroke='#6A3D9A' stroke-width='8'/>")
AddText $bandSvg 480 710 "formal max-gap" 12
[void]$bandSvg.AppendLine("<line x1='640' y1='705' x2='680' y2='705' stroke='#1B9E77' stroke-width='8'/>")
AddText $bandSvg 690 710 "intended min-gap" 12
[void]$bandSvg.AppendLine("</svg>")
$bandPath = Join-Path $figureDir "safe_k_bands.svg"
[System.IO.File]::WriteAllText(
    $bandPath,
    $bandSvg.ToString().Replace("`r`n", "`n").TrimEnd("`r", "`n") + "`n",
    $utf8NoBom
)

Write-Output $performancePath
Write-Output $bandPath
