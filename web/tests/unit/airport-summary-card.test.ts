/** Tests for the forecast-map airport card's plain Temp / Dew row (#948). */

import { describe, it, expect } from 'vitest';
import { tempDewCell } from '../../ts/visualization/airport-summary-card';

describe('tempDewCell', () => {
  it('shows temperature and dewpoint, rounded', () => {
    expect(tempDewCell({ temperature_c: 4.4, dewpoint_c: 1.6 })).toBe('4° / 2°');
  });

  it('keeps the temperature when dewpoint is missing (older response)', () => {
    expect(tempDewCell({ temperature_c: -3 })).toBe('-3° / —');
    expect(tempDewCell({ temperature_c: 7, dewpoint_c: null })).toBe('7° / —');
  });

  it('is a plain dash when the model has neither value', () => {
    expect(tempDewCell({ temperature_c: null, dewpoint_c: null })).toBe('—');
    expect(tempDewCell(undefined)).toBe('—');
  });
});
