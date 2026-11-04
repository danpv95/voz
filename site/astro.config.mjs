import { defineConfig } from 'astro/config';

// URL pública del sitio. En Vercel se puede fijar con SITE_URL.
const site = (process.env.SITE_URL || 'https://voz-cli.vercel.app').replace(/\/+$/, '');

export default defineConfig({
  site,
  trailingSlash: 'never',
  compressHTML: true,
  devToolbar: { enabled: false },
  // Sin scripts inline: la CSP del sitio (vercel.json) es `script-src 'self'`.
  vite: { build: { assetsInlineLimit: 0 } },
});
