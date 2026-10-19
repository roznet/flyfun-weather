#!/usr/bin/env node
/**
 * Build the MCP App widget (#819) into ONE self-contained HTML file,
 * dist/mcp-cross-section.html: the host sandbox loads no external script or
 * stylesheet, so the bundle (no code splitting: the locale JSON is inlined
 * too) and the CSS go inside the page. The MCP server reads this file.
 *
 *   node scripts/build-mcp-app.mjs           # build once
 *   node scripts/build-mcp-app.mjs --watch   # rebuild on change (dev)
 */

import * as esbuild from 'esbuild';
import { mkdirSync, readFileSync, watch, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const web = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const TEMPLATE = resolve(web, 'mcp-app/cross-section.html');
const CSS = resolve(web, 'mcp-app/cross-section.css');
const OUT = resolve(web, 'dist/mcp-cross-section.html');

/** The page with `js` and `css` inlined. Replacer functions, so a `$` in the
 *  bundle is never read as a replacement pattern; `</script` is escaped so a
 *  string in the bundle can't close the inline script element. */
function assemble(template, js, css) {
  const safeJs = js.replace(/<\/script/gi, '<\\/script');
  return template
    .replace('/*__INLINE_CSS__*/', () => css)
    .replace('/*__INLINE_JS__*/', () => safeJs);
}

/** The widget is English only: i18n.ts's dynamic `./locales/${locale}.json`
 *  import would inline every locale (~70 KB each), so the non-English ones
 *  become empty objects and `t()` falls back to English. */
const englishOnly = {
  name: 'english-only',
  setup(build) {
    // esbuild filters are Go regexps (no lookahead): name the other locales.
    build.onLoad({ filter: /[\\/]i18n[\\/]locales[\\/](fr|de|es)\.json$/ }, () => ({
      contents: '{}',
      loader: 'json',
    }));
  },
};

const options = {
  entryPoints: [resolve(web, 'ts/mcp-app/cross-section-main.ts')],
  plugins: [englishOnly],
  bundle: true,
  write: false,
  format: 'iife',
  target: 'es2020',
  minify: true,
  legalComments: 'none',
  logLevel: 'warning',
};

function writePage(js) {
  mkdirSync(dirname(OUT), { recursive: true });
  const html = assemble(readFileSync(TEMPLATE, 'utf8'), js, readFileSync(CSS, 'utf8'));
  writeFileSync(OUT, html);
  console.log(`mcp-app: wrote ${OUT} (${Math.round(html.length / 1024)} KB)`);
}

if (process.argv.includes('--watch')) {
  let lastJs = '';
  const ctx = await esbuild.context({
    ...options,
    plugins: [englishOnly, {
      name: 'write-page',
      setup(build) {
        build.onEnd((result) => {
          if (result.errors.length || !result.outputFiles?.length) return;
          lastJs = result.outputFiles[0].text;
          writePage(lastJs);
        });
      },
    }],
  });
  await ctx.watch();
  // esbuild watches the TS graph; the template and CSS are outside it.
  for (const f of [TEMPLATE, CSS]) watch(f, () => { if (lastJs) writePage(lastJs); });
} else {
  const result = await esbuild.build(options);
  writePage(result.outputFiles[0].text);
}
