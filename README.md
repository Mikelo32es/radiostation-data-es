# RadioStation ES Catalog (solo España)

Este repositorio genera y publica un **catálogo JSON** de emisoras **solo de España (ES)** para que tu app (RadioStation / Radio4Station) lo consuma desde GitHub.

## Qué se genera

- `catalog/es/stations.json` → lista de emisoras (solo ES)
- `catalog/es/sections.json` → secciones sugeridas para la Home
- `catalog/es/manifest.json` → metadatos (fecha, versión, conteos)

> Fuente: Radio Browser (API pública) — el workflow descarga, limpia duplicados y genera los archivos finales.

## Cómo usarlo (GitHub)

1. Copia **tal cual** las carpetas:
   - `.github/`
   - `catalog/`
   - `tools/`
   - `README.md`
2. Haz `commit` y `push`.
3. En GitHub → **Actions** → ejecuta `Generate ES Radio Catalog`.
4. Tras terminar, tendrás los JSON en `catalog/es/`.

## Para tu app

Consume el archivo RAW:

- `catalog/es/stations.json`

(En el siguiente paso lo conectamos al `StationRepository` de la app para que deje de ser “mock”.)
