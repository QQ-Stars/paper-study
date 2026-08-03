(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (!root || !root.document) return;

  const controller = api.createAppearanceController({
    document: root.document,
    getStorage: () => root.localStorage,
    CustomEvent: root.CustomEvent,
  });
  controller.bootstrap();
  root.PaperStudyAppearance = { ...api, controller };

  const bind = () => controller.bindControls();
  if (root.document.readyState === 'loading') {
    root.document.addEventListener('DOMContentLoaded', bind, { once: true });
  } else {
    bind();
  }
})(typeof window !== 'undefined' ? window : undefined, function () {
  const DEFAULT_APPEARANCE = Object.freeze({ uiStyle: 'classic', theme: 'light' });
  const STORAGE_KEYS = Object.freeze({ uiStyle: 'paperstudy.uiStyle', theme: 'theme' });
  const EVENT_NAME = 'paperstudy:appearancechange';
  const VALID_UI_STYLES = new Set(['classic', 'spatial']);
  const VALID_THEMES = new Set(['light', 'dark']);

  function copy(state) { return { uiStyle: state.uiStyle, theme: state.theme }; }
  function validPair(state) {
    return VALID_UI_STYLES.has(state.uiStyle) && VALID_THEMES.has(state.theme);
  }
  function normalizePair(candidate) {
    return validPair(candidate || {}) ? copy(candidate) : copy(DEFAULT_APPEARANCE);
  }

  function createAppearanceController({ document, storage, getStorage, CustomEvent } = {}) {
    let state = copy(DEFAULT_APPEARANCE);
    let controlsBound = false;

    function resolveStorage() {
      if (storage) return storage;
      return typeof getStorage === 'function' ? getStorage() : null;
    }

    function readState() {
      try {
        const store = resolveStorage();
        if (!store) return copy(DEFAULT_APPEARANCE);
        const storedStyle = store.getItem(STORAGE_KEYS.uiStyle);
        const storedTheme = store.getItem(STORAGE_KEYS.theme);
        const candidate = {
          uiStyle: storedStyle == null ? DEFAULT_APPEARANCE.uiStyle : storedStyle,
          theme: storedTheme == null ? DEFAULT_APPEARANCE.theme : storedTheme,
        };
        return normalizePair(candidate);
      } catch (error) {
        return copy(DEFAULT_APPEARANCE);
      }
    }

    function applyRoot() {
      const root = document && document.documentElement;
      if (!root || typeof root.setAttribute !== 'function') return;
      root.setAttribute('data-ui-style', state.uiStyle);
      root.setAttribute('data-theme', state.theme);
    }

    function controls(field) {
      if (!document || typeof document.querySelectorAll !== 'function') return [];
      return Array.from(document.querySelectorAll(`[data-appearance-field="${field}"]`));
    }

    function themeButton() {
      return document && typeof document.querySelector === 'function'
        ? document.querySelector('#themeBtn')
        : null;
    }

    function syncControls() {
      for (const control of controls('uiStyle')) control.checked = control.value === state.uiStyle;
      for (const control of controls('theme')) control.checked = control.value === state.theme;
      const button = themeButton();
      if (!button) return;
      button.textContent = state.theme === 'dark' ? 'Light' : 'Dark';
      button.setAttribute(
        'aria-label',
        state.theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme',
      );
    }

    function persist() {
      try {
        const store = resolveStorage();
        if (!store) return;
        store.setItem(STORAGE_KEYS.uiStyle, state.uiStyle);
        store.setItem(STORAGE_KEYS.theme, state.theme);
      } catch (error) {
        // Local appearance remains usable when storage is unavailable or full.
      }
    }

    function emit() {
      if (!document || typeof document.dispatchEvent !== 'function' || typeof CustomEvent !== 'function') return;
      document.dispatchEvent(new CustomEvent(EVENT_NAME, { detail: copy(state) }));
    }

    function bootstrap() {
      state = readState();
      applyRoot();
      return copy(state);
    }

    function getState() { return copy(state); }

    function setAppearance(partial = {}) {
      const next = normalizePair({ ...state, ...partial });
      if (next.uiStyle === state.uiStyle && next.theme === state.theme) return copy(state);
      state = next;
      applyRoot();
      syncControls();
      persist();
      emit();
      return copy(state);
    }

    function bindControls() {
      syncControls();
      if (controlsBound) return;
      controlsBound = true;
      for (const control of controls('uiStyle')) {
        control.addEventListener('change', () => {
          if (control.checked) setAppearance({ uiStyle: control.value });
        });
      }
      for (const control of controls('theme')) {
        control.addEventListener('change', () => {
          if (control.checked) setAppearance({ theme: control.value });
        });
      }
      const button = themeButton();
      if (button) {
        button.addEventListener('click', () => {
          setAppearance({ theme: state.theme === 'dark' ? 'light' : 'dark' });
        });
      }
    }

    return { bootstrap, bindControls, getState, setAppearance };
  }

  return { createAppearanceController, DEFAULT_APPEARANCE, STORAGE_KEYS };
});
