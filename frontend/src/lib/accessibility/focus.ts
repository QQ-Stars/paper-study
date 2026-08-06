const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function focusSafely(
  element: HTMLElement | null | undefined,
  preventScroll = true,
): boolean {
  if (!element || !element.isConnected) {
    return false;
  }

  element.focus({ preventScroll });
  return document.activeElement === element;
}

export function focusElementById(id: string): boolean {
  return focusSafely(document.getElementById(id));
}

export function focusPageTitle(): boolean {
  return focusSafely(document.getElementById('workspace-page-title'), false);
}

export function focusMainContent(): boolean {
  return focusSafely(document.getElementById('workspace-main'), false);
}

export function restoreFocus(triggerId: string | null): boolean {
  if (triggerId && focusElementById(triggerId)) {
    return true;
  }

  return focusElementById('workspace-command-trigger');
}

export function focusFirstWithin(container: HTMLElement): boolean {
  const preferredTarget = container.querySelector<HTMLElement>(
    '[data-panel-autofocus="true"]',
  );
  if (focusSafely(preferredTarget)) {
    return true;
  }

  const target = container.querySelector<HTMLElement>(focusableSelector);
  return focusSafely(target) || focusSafely(container);
}

export function trapTabKey(container: HTMLElement, event: KeyboardEvent): void {
  if (event.key !== 'Tab') {
    return;
  }

  const focusable = [...container.querySelectorAll<HTMLElement>(focusableSelector)].filter(
    (element) => !element.hidden && element.getAttribute('aria-hidden') !== 'true',
  );

  if (focusable.length === 0) {
    event.preventDefault();
    focusSafely(container);
    return;
  }

  const first = focusable[0];
  const last = focusable.at(-1);
  const active = document.activeElement;

  if (event.shiftKey && active === first) {
    event.preventDefault();
    focusSafely(last);
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    focusSafely(first);
  }
}
