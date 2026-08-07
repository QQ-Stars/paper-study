import { useRef, type RefObject } from 'react';

import { gsap, useGSAP } from './gsap';

export interface UseDeckFlipOptions {
  readonly scope: RefObject<HTMLElement | null>;
  readonly layoutKey: string;
  readonly enabled?: boolean;
}

const finalInlinePresentation = {
  clearProps: 'transform,opacity,visibility',
} as const;

interface DeckPresentationState {
  lastSetup: {
    readonly enabled: boolean;
    readonly layoutKey: string;
  } | null;
  hasPresented: boolean;
}

export function useDeckFlip({
  scope,
  layoutKey,
  enabled = true,
}: UseDeckFlipOptions): void {
  const presentation = useRef<DeckPresentationState>({
    hasPresented: false,
    lastSetup: null,
  });

  useGSAP(() => {
    const repeatsCurrentSetup = presentation.current.lastSetup?.layoutKey === layoutKey
      && presentation.current.lastSetup.enabled === enabled;
    presentation.current.lastSetup = { enabled, layoutKey };
    // React StrictMode replays the initial layout effect with the same inputs.
    // Let that committed replay replace the entrance reverted by its probe.
    let canPlayEntrance = !presentation.current.hasPresented || repeatsCurrentSetup;

    const present = (reduceMotion: boolean) => {
      const cards = Array.from(
        scope.current?.querySelectorAll<HTMLElement>('[data-deck-card]') ?? [],
      );
      if (cards.length === 0) return;

      if (!enabled || reduceMotion) {
        return;
      }

      if (!canPlayEntrance) {
        // The deck slots already encode their complete geometry in CSS transforms.
        // Replaying that geometry through FLIP makes wrap-around cards cross the
        // selected slot, so subsequent selections commit directly to their slots.
        gsap.set(cards, {
          clearProps: finalInlinePresentation.clearProps,
        });
        return;
      }

      canPlayEntrance = false;
      presentation.current.hasPresented = true;
      gsap.timeline({ defaults: { ease: 'power2.out' } }).fromTo(
        cards,
        { autoAlpha: 0, y: 10 },
        {
          autoAlpha: 1,
          y: 0,
          duration: 0.24,
          stagger: 0.035,
          clearProps: finalInlinePresentation.clearProps,
        },
      );
    };
    if (
      typeof window === 'undefined'
      || typeof window.matchMedia !== 'function'
    ) {
      present(true);
      return;
    }

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      present(true);
      return;
    }

    const media = gsap.matchMedia();

    media.add(
      {
        reduceMotion: '(prefers-reduced-motion: reduce)',
        allowMotion: '(prefers-reduced-motion: no-preference)',
      },
      (context) => {
        present(Boolean(context.conditions?.reduceMotion));
      },
    );

    return () => media.revert();
  }, {
    dependencies: [layoutKey, enabled],
    scope,
    revertOnUpdate: true,
  });
}
