import { beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  registerPlugin: vi.fn(),
  useGSAP: vi.fn(),
  Flip: { getState: vi.fn(), from: vi.fn() },
}));

vi.mock('gsap', () => ({
  gsap: { registerPlugin: mocks.registerPlugin },
}));
vi.mock('@gsap/react', () => ({ useGSAP: mocks.useGSAP }));
vi.mock('gsap/Flip', () => ({ Flip: mocks.Flip }));

beforeEach(() => {
  vi.resetModules();
  mocks.registerPlugin.mockClear();
});

it('registers useGSAP and Flip together at module bootstrap', async () => {
  await import('./gsap');

  expect(mocks.registerPlugin).toHaveBeenCalledOnce();
  expect(mocks.registerPlugin).toHaveBeenCalledWith(mocks.useGSAP, mocks.Flip);
});
