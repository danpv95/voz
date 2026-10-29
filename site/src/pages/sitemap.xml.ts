import type { APIRoute } from 'astro';
import { getReleases } from '../lib/load.ts';

export const GET: APIRoute = async ({ site }) => {
  const base = site!.href.replace(/\/$/, '');
  const releases = await getReleases();
  const urls = [
    { loc: `${base}/` },
    { loc: `${base}/cambios` },
    ...releases.filter((r) => !r.unreleased).map((r) => ({ loc: `${base}/cambios/${r.version}`, lastmod: r.date })),
  ];
  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.map((u) => `  <url><loc>${u.loc}</loc>${'lastmod' in u && u.lastmod ? `<lastmod>${u.lastmod}</lastmod>` : ''}</url>`).join('\n')}
</urlset>
`;
  return new Response(xml, { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
};
