# video-speed-converter

Conversor de videos por lotes

## Requisitos

- Python 3.10 o superior
- FFmpeg instalado y disponible en PATH

Instalacion de FFmpeg en Windows con Chocolatey:

```powershell
choco install ffmpeg -y
```

Verificar FFmpeg:

```powershell
ffmpeg -version
```

## Ejecucion

```powershell
python .\conversor_ffmpeg.py
```

## Ajuste de rendimiento

El script ahora ajusta automaticamente el rendimiento:

- Selecciona codec de video automaticamente (`h264_nvenc`, `h264_qsv`, `h264_amf` o `libx264`).
- Si el codec por hardware falla, usa `libx264` como respaldo.
- Balancea procesos en paralelo y hilos por proceso para evitar sobrecarga de CPU.

Variables opcionales:

```powershell
# Fuerza cantidad de procesos en paralelo
$env:CONVERSOR_WORKERS="2"

# Fuerza codec de video (ejemplo: libx264, h264_nvenc, h264_qsv, h264_amf)
$env:CONVERSOR_VIDEO_CODEC="libx264"

python .\conversor_ffmpeg.py
```
