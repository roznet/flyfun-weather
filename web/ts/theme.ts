/** Theme management — light / dark / system with localStorage persistence. */

export type ThemeChoice = 'light' | 'dark' | 'system';
export type EffectiveTheme = 'light' | 'dark';

const STORAGE_KEY = 'wb_theme';
const darkMq = window.matchMedia('(prefers-color-scheme: dark)');

/** Read the stored theme choice from localStorage. */
export function getTheme(): ThemeChoice {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (raw === 'light' || raw === 'dark' || raw === 'system') return raw;
  return 'system';
}

/** Resolve theme choice to an effective light/dark value. */
export function getEffectiveTheme(choice?: ThemeChoice): EffectiveTheme {
  const c = choice ?? getTheme();
  if (c === 'system') return darkMq.matches ? 'dark' : 'light';
  return c;
}

/** Persist and apply a theme choice. */
export function setTheme(choice: ThemeChoice): void {
  localStorage.setItem(STORAGE_KEY, choice);
  applyTheme(choice);
  updateToggleUI(choice);
}

/** Apply the resolved theme to the document. */
function applyTheme(choice: ThemeChoice): void {
  const effective = getEffectiveTheme(choice);
  document.documentElement.dataset.theme = effective;
  window.dispatchEvent(new CustomEvent('theme-changed', { detail: effective }));
}

/** Update toggle button states to match current choice. */
function updateToggleUI(choice: ThemeChoice): void {
  document.querySelectorAll('.theme-toggle .btn-toggle').forEach((btn) => {
    btn.classList.toggle('active', (btn as HTMLElement).dataset.theme === choice);
  });
}

/** Inject the 3-segment toggle into .header-right. */
function injectToggle(): void {
  const headerRight = document.querySelector('.header-right');
  if (!headerRight || headerRight.querySelector('.theme-toggle')) return;

  const wrap = document.createElement('div');
  wrap.className = 'theme-toggle';

  const choices: { choice: ThemeChoice; label: string; title: string }[] = [
    { choice: 'light', label: '☀', title: 'Light' },
    { choice: 'system', label: '◐', title: 'System' },
    { choice: 'dark', label: '☾', title: 'Dark' },
  ];

  const current = getTheme();
  for (const { choice, label, title } of choices) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn-toggle' + (choice === current ? ' active' : '');
    btn.dataset.theme = choice;
    btn.title = title;
    btn.textContent = label;
    btn.addEventListener('click', () => setTheme(choice));
    wrap.appendChild(btn);
  }

  headerRight.prepend(wrap);
}

/** Initialize theme — call once from each entry point. */
export function initTheme(): void {
  applyTheme(getTheme());
  injectToggle();

  // Listen for OS theme changes when in system mode
  darkMq.addEventListener('change', () => {
    if (getTheme() === 'system') {
      applyTheme('system');
    }
  });
}
