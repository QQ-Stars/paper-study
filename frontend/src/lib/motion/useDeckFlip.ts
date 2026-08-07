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

interface DeckPresentationState {
  previousLayout: Flip.FlipState | null;
  lastSetup: {
    readonly enabled: boolean;
    readonly layoutKey: string;
  } | null;
  hasPresented: boolean;
}

function clearInlinePresentation(cards: readonly HTMLElement[]): void {
  cards.forEach((card) => {
    card.style.removeProperty('transform');
    card.style.removeProperty('opacity');
    card.style.removeProperty('visibility');
  });
}

export function useDeckFlip({
  scope,
  layoutKey,
  enabled = true,
}: UseDeckFlipOptions): void {
  const presentation = useRef<DeckPresentationState>({
    hasPresented: false,
    lastSetup: null,
    previousLayout: null,
  });

  useGSAP(() => {
    const repeatsCurrentSetup = presentation.current.lastSetup?.layoutKey === layoutKey
      && presentation.current.lastSetup.enabled === enabled;
    presentation.current.lastSetup = { enabled, layoutKey };
    // React StrictMode replays the initial layout effect with the same inputs.
    // Let that committed replay replace the entrance reverted by its probe.
    let canPlayEntrance = !presentation.current.hasPresented || repeatsCurrentSetup;

    const resetCommittedLayout = () => {
      presentation.current.previousLayout = null;
    };

    const present = (reduceMotion: boolean) => {
      const cards = Array.from(
        scope.current?.querySelectorAll<HTMLElement>('[data-deck-card]') ?? [],
      );
      if (cards.length === 0) {
        resetCommittedLayout();
        return;
      }

      // matchMedia owns a nested GSAP context. Explicitly stop any tween left
      // by a rapidly superseded layout before measuring the new CSS slots.
      Flip.killFlipsOf(cards, false);
      gsap.killTweensOf(cards);
      clearInlinePresentation(cards);

      if (!enabled || reduceMotion) {
        resetCommittedLayout();
        return;
      }

      const committedLayout = Flip.getState(cards, { props: 'opacity' });
      const previousLayout = presentation.current.previousLayout;

      if (canPlayEntrance || previousLayout === null) {
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
      } else {
        Flip.from(previousLayout, {
          targets: cards,
          duration: 0.32,
          ease: 'power3.out',
          fade: true,
          scale: true,
          simple: true,
          clearProps: finalInlinePresentation.clearProps,
          onComplete: () => clearInlinePresentation(cards),
          onInterrupt: () => clearInlinePresentation(cards),
        });
      }

      presentation.current.previousLayout = committedLayout;
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
