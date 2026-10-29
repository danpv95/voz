import type { APIRoute } from 'astro';
import { getReleases } from '../lib/load.ts';
import { buildFeed } from '../lib/feed.ts';

export const GET: APIRoute = async ({ site }) => {
  const releases = await getReleases();
  return new Response(buildFeed(releases, site!.href.replace(/\/$/, '')), {
    headers: { 'Content-Type': 'application/rss+xml; charset=utf-8' },
  });
};
