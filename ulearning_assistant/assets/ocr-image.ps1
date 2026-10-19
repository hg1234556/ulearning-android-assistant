param(
    [Parameter(Mandatory = $true)]
    [string]$ImagePath
)

# The only input is the caller's existing local PNG. No capture, UI control,
# network calls, extra files, or system configuration changes are performed.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$bitmap = $null
$convertedBitmap = $null
$stream = $null
$failureCode = 'OCR_FAILED'

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    [void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    [void][Windows.Storage.Streams.IRandomAccessStreamWithContentType, Windows.Storage.Streams, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.BitmapPixelFormat, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.BitmapAlphaMode, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
    [void][Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType = WindowsRuntime]
    [void][Windows.Media.Ocr.OcrResult, Windows.Media.Ocr, ContentType = WindowsRuntime]

    $failureCode = 'ASYNC_BRIDGE_UNAVAILABLE'
    $script:asTaskMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq 'AsTask' -and $_.IsGenericMethodDefinition -and
            $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
        } | Select-Object -First 1
    if ($null -eq $script:asTaskMethod) { throw 'bridge' }

    function Wait-WinRtOperation {
        param($Operation, [Type]$ResultType)
        $method = $script:asTaskMethod.MakeGenericMethod($ResultType)
        $task = $method.Invoke($null, @($Operation))
        $task.Wait()
        return $task.Result
    }

    $failureCode = 'IMAGE_READ_FAILED'
    $localPath = [System.IO.Path]::GetFullPath($ImagePath)
    if ($localPath.StartsWith('\\') -or
        [System.IO.Path]::GetExtension($localPath).ToLowerInvariant() -ne '.png' -or
        -not [System.IO.File]::Exists($localPath)) { throw 'input' }
    $file = Wait-WinRtOperation `
        ([Windows.Storage.StorageFile]::GetFileFromPathAsync($localPath)) `
        ([Windows.Storage.StorageFile])
    $stream = Wait-WinRtOperation ($file.OpenReadAsync()) `
        ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
    $decoder = Wait-WinRtOperation `
        ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) `
        ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Wait-WinRtOperation ($decoder.GetSoftwareBitmapAsync()) `
        ([Windows.Graphics.Imaging.SoftwareBitmap])
    $convertedBitmap = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert(
        $bitmap, [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)

    $failureCode = 'IMAGE_TOO_LARGE'
    if ($convertedBitmap.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or
        $convertedBitmap.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) {
        throw 'dimensions'
    }
    $failureCode = 'OCR_UNAVAILABLE'
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) { throw 'language' }
    $failureCode = 'OCR_FAILED'
    $result = Wait-WinRtOperation ($engine.RecognizeAsync($convertedBitmap)) `
        ([Windows.Media.Ocr.OcrResult])
    $lines = @(
        foreach ($line in $result.Lines) {
            $words = @(
                foreach ($word in $line.Words) {
                    $rectangle = $word.BoundingRect
                    @{
                        text = $word.Text
                        bounds = @([double]$rectangle.X, [double]$rectangle.Y,
                                   [double]$rectangle.Width, [double]$rectangle.Height)
                    }
                }
            )
            @{ text = $line.Text; words = $words }
        }
    )
    @{
        width = [int]$convertedBitmap.PixelWidth
        height = [int]$convertedBitmap.PixelHeight
        language = $engine.RecognizerLanguage.LanguageTag
        lines = $lines
    } | ConvertTo-Json -Depth 10 -Compress
    exit 0
}
catch {
    @{ error = $failureCode } | ConvertTo-Json -Compress
    exit 1
}
finally {
    if ($null -ne $convertedBitmap) { $convertedBitmap.Dispose() }
    if ($null -ne $bitmap) { $bitmap.Dispose() }
    if ($null -ne $stream) { $stream.Dispose() }
}
