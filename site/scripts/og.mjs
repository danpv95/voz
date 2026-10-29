// Genera public/og.png (1200x630) desde scripts/og.html con el Playwright de DEV/Tools/doc-scraper.
// Uso: npm run og   (PLAYWRIGHT_PATH para otra ruta)
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const pw = process.env.PLAYWRIGHT_PATH || resolve(here, '../../../doc-scraper/node_modules/playwright');
const { chromium } = createRequire(import.meta.url)(pw);

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
  await page.goto(`file://${resolve(here, 'og.html')}`);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: resolve(here, '../public/og.png') });
  console.log('public/og.png listo');
} finally {
  await browser.close();
}
