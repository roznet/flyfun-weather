import { afterEach, describe, expect, it, vi } from 'vitest';

import { CrossSectionRenderer } from '../../ts/visualization/cross-section/renderer';

class FakeResizeObserver {
  observe = vi.fn();
  disconnect = vi.fn();

  constructor(_callback: ResizeObserverCallback) {}
}

function fakeCanvas(): HTMLCanvasElement {
  return {
    style: { cssText: '' },
    remove: vi.fn(),
  } as unknown as HTMLCanvasElement;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('CrossSectionRenderer lifecycle', () => {
  it('removes its exact theme listener and cannot render after destroy', () => {
    const windowTarget = new EventTarget();
    const addListener = vi.spyOn(windowTarget, 'addEventListener');
    const removeListener = vi.spyOn(windowTarget, 'removeEventListener');
    const canvases = [fakeCanvas(), fakeCanvas()];
    vi.stubGlobal('window', windowTarget);
    vi.stubGlobal('document', {
      createElement: vi.fn(() => canvases.shift()),
    });
    vi.stubGlobal('ResizeObserver', FakeResizeObserver);
    const container = {
      appendChild: vi.fn(),
    } as unknown as HTMLElement;

    const renderer = new CrossSectionRenderer(container);
    const render = vi.spyOn(renderer, 'render');
    const themeListener = addListener.mock.calls.find(
      ([type]) => type === 'theme-changed',
    )?.[1];
    expect(themeListener).toBeTypeOf('function');

    renderer.destroy();
    windowTarget.dispatchEvent(new Event('theme-changed'));

    expect(removeListener).toHaveBeenCalledWith('theme-changed', themeListener);
    expect(render).not.toHaveBeenCalled();
  });
});
