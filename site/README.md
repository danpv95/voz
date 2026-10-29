# Sitio de voz

Sitio estático hecho con [Astro](https://astro.build) (sin frameworks de UI). Necesita Node 22.22 o más reciente.

## Correrlo

```bash
cd site
npm install
npm run dev       # desarrollo en http://localhost:4321
npm run build     # genera dist/
npm run preview   # sirve dist/ para revisarlo
npm test          # pruebas del parser del CHANGELOG
npm run typecheck # tsc estricto sobre src, tests y scripts
```

`SITE_URL` fija la URL pública para canonical, Open Graph, sitemap y RSS (por defecto `https://voz-cli.vercel.app`).

## Cómo se genera el historial

`/cambios`, `/cambios/<versión>`, `/feed.xml` y `/sitemap.xml` salen de `../CHANGELOG.md`, leído y parseado en cada build por `src/lib/changelog.ts` (función pura, probada en `tests/changelog.test.ts`). Para publicar una versión nueva basta con editar el CHANGELOG y volver a desplegar. La prueba compara las versiones publicadas con `git tag`.

Si el build no encuentra `../CHANGELOG.md` (Vercel sin archivos fuera del root directory), lee el de `main` en GitHub y avisa en el log. La guía PDF se enlaza solo si el release la tiene (comprobación en build, con respaldo al release si no hay red).

El filtro `?tipo=corregido` se resuelve sin JS con las páginas `/cambios/tipo/*` y una reescritura en `vercel.json`; con JS se aplica en el mismo documento.

## Imagen para compartir

`public/og.png` (1200x630) se genera con `npm run og` desde `scripts/og.html`. Necesita Playwright; por defecto usa el de `DEV/Tools/doc-scraper` (`PLAYWRIGHT_PATH` para otra ruta).

## Desplegar en Vercel

1. Importa el repositorio.
2. Root directory: `site`. Framework preset: Astro. Node 22.
3. Build: `npm run build`. Salida: `dist`.
4. Opcional: variable `SITE_URL` con el dominio final.

Sin cookies, sin analítica y sin peticiones a terceros desde el navegador. La fuente (Fraunces, OFL, subconjunto latino) va auto-hospedada en `public/fonts/`.
