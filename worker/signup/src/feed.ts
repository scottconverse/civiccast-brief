// SPDX-License-Identifier: Apache-2.0
// Reads the items of a CivicCast Brief podcast feed (our own RSS output, see site/feed.py).

export interface FeedItem {
  guid: string;
  title: string;
  link: string;
  description: string;
}

const ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };

export function decodeXml(text: string): string {
  const cdata = text.match(/^\s*<!\[CDATA\[([\s\S]*?)\]\]>\s*$/);
  if (cdata) return (cdata[1] ?? "").trim();
  return text
    .replace(/&(#x[0-9a-fA-F]+|#[0-9]+|[a-z]+);/g, (whole, name: string) => {
      if (name.startsWith("#x")) return String.fromCodePoint(parseInt(name.slice(2), 16));
      if (name.startsWith("#")) return String.fromCodePoint(parseInt(name.slice(1), 10));
      return ENTITIES[name] ?? whole;
    })
    .trim();
}

function tag(block: string, name: string): string {
  const m = block.match(new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)</${name}>`));
  return m ? decodeXml(m[1] ?? "") : "";
}

export function parseFeed(xml: string): FeedItem[] {
  const items: FeedItem[] = [];
  for (const m of xml.matchAll(/<item>([\s\S]*?)<\/item>/g)) {
    const block = m[1] ?? "";
    const link = tag(block, "link");
    const guid = tag(block, "guid") || link;
    const title = tag(block, "title");
    if (!guid || !title || !/^https:\/\//.test(link)) continue;
    items.push({ guid, title, link, description: tag(block, "description") });
  }
  return items;
}
