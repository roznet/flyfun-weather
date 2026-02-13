/** Settings page entry point — preferences form management. */

import { fetchCurrentUser, logout } from './adapters/auth-adapter';
import {
  fetchPreferences,
  savePreferences,
  clearAutorouterCreds,
  type PreferencesResponse,
} from './adapters/preferences-adapter';

const ALL_MODELS = ['gfs', 'ecmwf', 'icon'] as const;

async function init(): Promise<void> {
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  renderUserInfo(user.name);

  try {
    const prefs = await fetchPreferences();
    populateForm(prefs);
  } catch (err) {
    showStatus(`Failed to load preferences: ${err}`, true);
  }

  // Save button
  const form = document.getElementById('settings-form') as HTMLFormElement;
  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    await handleSave();
  });

  // Clear autorouter credentials
  const clearBtn = document.getElementById('clear-autorouter-btn');
  clearBtn?.addEventListener('click', async () => {
    try {
      await clearAutorouterCreds();
      (document.getElementById('input-ar-username') as HTMLInputElement).value = '';
      (document.getElementById('input-ar-password') as HTMLInputElement).value = '';
      updateAutorouterStatus(false);
      showStatus('Autorouter credentials cleared.');
    } catch (err) {
      showStatus(`Failed to clear credentials: ${err}`, true);
    }
  });
}

function populateForm(prefs: PreferencesResponse): void {
  const d = prefs.defaults;
  if (d.cruise_altitude_ft != null) {
    (document.getElementById('input-altitude') as HTMLInputElement).value = String(d.cruise_altitude_ft);
  }
  if (d.flight_ceiling_ft != null) {
    (document.getElementById('input-ceiling') as HTMLInputElement).value = String(d.flight_ceiling_ft);
  }

  // Models checkboxes
  const selectedModels = d.models || [...ALL_MODELS];
  for (const m of ALL_MODELS) {
    const cb = document.getElementById(`model-${m}`) as HTMLInputElement;
    if (cb) cb.checked = selectedModels.includes(m);
  }

  updateAutorouterStatus(prefs.has_autorouter_creds);
}

async function handleSave(): Promise<void> {
  const altitude = parseInt((document.getElementById('input-altitude') as HTMLInputElement).value, 10);
  const ceiling = parseInt((document.getElementById('input-ceiling') as HTMLInputElement).value, 10);

  const models: string[] = [];
  for (const m of ALL_MODELS) {
    const cb = document.getElementById(`model-${m}`) as HTMLInputElement;
    if (cb?.checked) models.push(m);
  }
  if (models.length === 0) {
    showStatus('Select at least one forecast model.', true);
    return;
  }

  const arUsername = (document.getElementById('input-ar-username') as HTMLInputElement).value.trim();
  const arPassword = (document.getElementById('input-ar-password') as HTMLInputElement).value.trim();

  try {
    const result = await savePreferences({
      defaults: {
        cruise_altitude_ft: isNaN(altitude) ? null : altitude,
        flight_ceiling_ft: isNaN(ceiling) ? null : ceiling,
        models,
      },
      autorouter_username: arUsername || undefined,
      autorouter_password: arPassword || undefined,
    });
    updateAutorouterStatus(result.has_autorouter_creds);
    // Clear password field after successful save
    if (arPassword) {
      (document.getElementById('input-ar-password') as HTMLInputElement).value = '';
    }
    showStatus('Preferences saved.');
  } catch (err) {
    showStatus(`Failed to save: ${err}`, true);
  }
}

function updateAutorouterStatus(hasCreds: boolean): void {
  const badge = document.getElementById('ar-status-badge');
  if (!badge) return;
  if (hasCreds) {
    badge.textContent = 'Configured';
    badge.className = 'badge badge-green';
  } else {
    badge.textContent = 'Not set';
    badge.className = 'badge badge-none';
  }
  const clearBtn = document.getElementById('clear-autorouter-btn') as HTMLButtonElement;
  if (clearBtn) clearBtn.style.display = hasCreds ? '' : 'none';
}

function showStatus(message: string, isError = false): void {
  const el = document.getElementById('status-message');
  if (!el) return;
  el.textContent = message;
  el.style.display = 'block';
  el.className = isError ? 'status-error' : 'status-success';
  if (!isError) {
    setTimeout(() => { el.style.display = 'none'; }, 3000);
  }
}

function renderUserInfo(name: string): void {
  const container = document.getElementById('user-info');
  if (!container) return;
  container.innerHTML = `
    <span class="user-name">${name}</span>
    <button class="btn-logout" id="logout-btn">Sign out</button>
  `;
  document.getElementById('logout-btn')?.addEventListener('click', () => logout());
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
