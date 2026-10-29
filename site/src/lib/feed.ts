// RSS 2.0 de versiones publicadas, desde el mismo parser que /cambios.
import type { Release } from './changelog.ts';
import { DESCRIPTION } from './site.ts';

const esc = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export function buildFeed(releases: Release[], site: string): string {
  const published = releases.filter((r) => !r.unreleased && r.date);
  const items = published
    .map((r) => {
      const link = `${site}/cambios/${r.version}`;
      const body = [r.intro[0], ...r.highlights.map((h) => `${h}.`)].filter(Boolean).join(' ');
      return `    <item>
      <title>voz ${esc(r.version!)}</title>
      <link>${esc(link)}</link>
      <guid isPermaLink="true">${esc(link)}</guid>
      <pubDate>${new Date(`${r.date}T12:00:00Z`).toUTCString()}</pubDate>
      <description>${esc(body)}</description>
    </item>`;
    })
    .join('\n');
  const last = published[0] ? new Date(`${published[0].date}T12:00:00Z`).toUTCString() : new Date(0).toUTCString();
  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>voz · versiones</title>
    <link>${esc(site)}/cambios</link>
    <description>${esc(DESCRIPTION)}</description>
    <language>es</language>
    <lastBuildDate>${last}</lastBuildDate>
    <atom:link href="${esc(site)}/feed.xml" rel="self" type="application/rss+xml"/>
${items}
  </channel>
</rss>
`;
}
