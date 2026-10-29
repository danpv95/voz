import { defineConfig } from 'astro/config';

// URL pública del sitio. En Vercel se puede fijar con SITE_URL.
const site = (process.env.SITE_URL || 'https://voz.vercel.app').replace(/\/+$/, '');

export default defineConfig({
  site,
  trailingSlash: 'never',
  compressHTML: true,
  devToolbar: { enabled: false },
});
