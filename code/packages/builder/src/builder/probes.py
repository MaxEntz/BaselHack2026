"""The JavaScript the crawler runs in each page: only text and numbers leave it.

`crawl.py` checks whatever comes back: the page's own scripts can replace what these call.
"""

# The page's text, title, description, element ids, JSON-LD blocks and links.
EXTRACT = """() => ({
  title: document.title,
  text: document.body ? document.body.innerText : "",
  description:
    document.querySelector('meta[name="description" i]')?.getAttribute("content") ?? null,
  ids: Array.from(document.querySelectorAll("[id]"), (element) => element.id).filter(Boolean),
  jsonLd: Array.from(
    document.querySelectorAll('script[type="application/ld+json" i]'),
    (script) => script.textContent,
  ),
  links: Array.from(document.querySelectorAll("a[href]"), (a) => {
    try {
      return [new URL(a.getAttribute("href"), document.baseURI).href, a.textContent ?? ""];
    } catch {
      return null;
    }
  }).filter(Boolean),
})"""


# The homepage's computed styles, for its look (#149): only numbers and colours, aggregated in the
# page, never the CSS itself. `builder.look` turns them into a `Look`. Fonts and images stay
# unloaded (`SKIPPED_RESOURCES`): a font's declared family is read, never its file.
LOOK = """() => {
  const LIMIT = 5000;
  const visible = (cs) => cs.display !== "none" && cs.visibility !== "hidden" && +cs.opacity > 0;
  const area = (r) => Math.max(0, r.width) * Math.max(0, r.height);
  const clear = (c) =>
    !c || c === "none" || c === "transparent" || /rgba\\([^)]*,\\s*0\\)$/.test(c);
  const bump = (map, key, n) => { map[key] = (map[key] || 0) + n; };
  const body = document.body;
  if (!body) return null;
  const bcs = getComputedStyle(body), hcs = getComputedStyle(document.documentElement);
  const look = {
    bg: clear(bcs.backgroundColor) ? hcs.backgroundColor : bcs.backgroundColor,
    ink: bcs.color, body_font: bcs.fontFamily, heading: null,
    backgrounds: {}, texts: {}, borders: {}, radii: [], blob_area: 0, textures: 0,
    page_area: area(document.documentElement.getBoundingClientRect()) || 1,
    page_width: document.documentElement.clientWidth || 1,
  };
  // The restaurant's name: the largest text of the header, the brand or the first headings.
  for (const el of document.querySelectorAll("header *, h1, h2, [class*=brand], [class*=logo]")) {
    const cs = getComputedStyle(el);
    if (!visible(cs) || !el.textContent.trim()) continue;
    const size = parseFloat(cs.fontSize);
    if (!look.heading || size > look.heading.size) {
      look.heading = { size, color: cs.color, font: cs.fontFamily };
    }
  }
  const behind = (el) => {
    for (let e = el; e; e = e.parentElement) {
      const c = getComputedStyle(e).backgroundColor;
      if (!clear(c)) return c;
    }
    return look.bg;
  };
  let seen = 0;
  for (const el of body.querySelectorAll("*")) {
    if (++seen > LIMIT) break;
    const cs = getComputedStyle(el);
    if (!visible(cs)) continue;
    const r = el.getBoundingClientRect(), a = area(r);
    if (/feTurbulence/.test(cs.backgroundImage) && a > 10000) look.textures += 1;
    if (el instanceof SVGElement) {
      const shape = ["path", "rect", "circle", "ellipse", "polygon"].includes(el.tagName);
      if (shape && !clear(cs.fill)) {
        bump(look.backgrounds, cs.fill, a);
        if (el.tagName === "path") look.blob_area += a;
      }
      continue;
    }
    if (!clear(cs.backgroundColor)) bump(look.backgrounds, cs.backgroundColor, a);
    for (const side of ["Top", "Right", "Bottom", "Left"]) {
      const drawn = cs["border" + side + "Style"] !== "none";
      if (parseFloat(cs["border" + side + "Width"]) > 0 && drawn) {
        bump(look.borders, cs["border" + side + "Color"], 1);
      }
    }
    const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim());
    const chars = own.join("").length;
    if (chars) bump(look.texts, cs.color + "|" + behind(el), chars);
    const boxed = !clear(cs.backgroundColor) || parseFloat(cs.borderTopWidth) > 0;
    if (boxed && a > 0 && look.radii.length < 500) {
      look.radii.push([parseFloat(cs.borderTopLeftRadius) || 0, r.width, r.height]);
    }
  }
  return look;
}"""


# What Studio's public tab needs (#151): each element's text and box on the page, to find a
# fact's quote on the screenshot. Only text and numbers leave the page.
BOXES = """(limit) => {
  const boxes = [];
  const body = document.body;
  if (!body) return null;
  for (const el of body.querySelectorAll("*")) {
    if (boxes.length >= limit) break;
    const text = (el.innerText || "").trim();
    if (!text || text.length > 1000) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    boxes.push({ text, x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY),
                 width: Math.round(r.width), height: Math.round(r.height) });
  }
  return { boxes, height: Math.max(document.documentElement.scrollHeight, body.scrollHeight) };
}"""
