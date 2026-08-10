param(
    [Parameter(Mandatory = $true)]
    [string]$ImagePath
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]

function Await-WinRt {
    param(
        [Parameter(Mandatory = $true)]$Operation,
        [Parameter(Mandatory = $true)][Type]$ResultType
    )

    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq 'AsTask' -and
            $_.IsGenericMethod -and
            $_.GetParameters().Count -eq 1
        } |
        Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.GetAwaiter().GetResult()
}

$resolvedPath = (Resolve-Path -LiteralPath $ImagePath).Path
$fileOperation = [Windows.Storage.StorageFile]::GetFileFromPathAsync($resolvedPath)
$file = Await-WinRt $fileOperation ([Windows.Storage.StorageFile])
$streamOperation = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read)
$stream = Await-WinRt $streamOperation ([Windows.Storage.Streams.IRandomAccessStream])
$decoderOperation = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
$decoder = Await-WinRt $decoderOperation ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmapOperation = $decoder.GetSoftwareBitmapAsync()
$bitmap = Await-WinRt $bitmapOperation ([Windows.Graphics.Imaging.SoftwareBitmap])

$language = [Windows.Globalization.Language]::new('zh-Hans-CN')
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if ($null -eq $engine) {
    throw 'Unable to create the zh-Hans-CN Windows OCR engine.'
}

$ocrOperation = $engine.RecognizeAsync($bitmap)
$result = Await-WinRt $ocrOperation ([Windows.Media.Ocr.OcrResult])
$result.Text

$bitmap.Dispose()
$stream.Dispose()
