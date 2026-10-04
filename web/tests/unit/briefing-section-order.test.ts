import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { NAV_GROUPS } from '../../ts/managers/sidebar-layout';

// The rail's SECTIONS nav and the main pane must list sections in the same
// order — otherwise scroll-spy jumps around and "next" in the nav is not
// "next" on the page.
describe('briefing section order', () => {
  const html = readFileSync(resolve(__dirname, '../../briefing.html'), 'utf8');
  const pageOrder = Array.from(html.matchAll(/data-section="([^"]+)"/g), (m) => m[1]);
  const navOrder = NAV_GROUPS.flatMap((g) => g.keys);

  it('every nav key is a section on the page', () => {
    for (const key of navOrder) expect(pageOrder).toContain(key);
  });

  it('page sections appear in nav order', () => {
    expect(pageOrder.filter((k) => navOrder.includes(k))).toEqual(navOrder);
  });
});
