import { useRef, type RefObject } from 'react';

import { Flip, gsap, useGSAP } from './gsap';

export interface UseDeckFlipOptions {
  readonly scope: RefObject<HTMLElement | null>;
  readonly layoutKey: string;
  readonly enabled?: boolean;
}

const finalInlinePresentation = {
  clearProps: 'transform,opacity,visibility',
} as const;

export function useDeckFlip({
  scope,
  layoutKey,
  enabled = true,
}: UseDeckFlipOptions): void {
  const previousLayout = useRef<Flip.FlipState | null>(null);

  useGSAP(() => {
    const present = (reduceMotion: boolean) => {
      const cards = Array.from(
        scope.current?.querySelectorAll<HTMLElement>('[data-deck-card]') ?? [],
      );
      if (cards.length === 0) {
        previousLayout.current = null;
        return;
      }

      if (!enabled || reduceMotion) {
        previousLayout.current = null;
        return;
      }

      const committedLayout = Flip.getState(cards, { props: 'opacity' });
      if (previousLayout.current == null) {
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
      } else {
        Flip.from(previousLayout.current, {
          targets: cards,
          duration: 0.32,
          ease: 'power3.out',
          fade: true,
          scale: true,
          simple: true,
          onComplete: () => gsap.set(cards, finalInlinePresentation),
        });
      }
      previousLayout.current = committedLayout;
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
