import { expect, test } from './fixtures/mockApi';

test.describe('Workspace layout regressions', () => {
  test('keeps deck slots stable when rapid moves interrupt the entrance', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/workspace/dashboard');

    const deck = page.locator('.paper-deck');
    await expect(deck.locator('[data-deck-card]')).toHaveCount(5);

    const run = await deck.evaluate(async (deckRoot) => {
      interface CardGeometry {
        readonly center: number;
        readonly inlineTransform: string;
        readonly layoutOffset: number;
        readonly paperId: string;
        readonly selected: boolean;
      }

      interface DeckGeometry {
        readonly elapsedMs: number;
        readonly stageCenter: number;
        readonly cards: readonly CardGeometry[];
      }

      interface DeckRun {
        readonly frames: readonly DeckGeometry[];
        readonly selectionIds: readonly string[];
      }

      const initialSelectedId = deckRoot
        .querySelector<HTMLElement>('[data-deck-card][aria-selected="true"]')
        ?.dataset.paperId;
      const nextButton = deckRoot
        .querySelector<HTMLButtonElement>('.paper-deck__actions button:last-child');
      const previousButton = deckRoot
        .querySelector<HTMLButtonElement>('.paper-deck__actions button:first-child');
      if (!initialSelectedId || !nextButton || !previousButton || nextButton.disabled) {
        throw new Error('The paper deck must start with enabled movement controls.');
      }

      const startedAt = performance.now();
      const frames: DeckGeometry[] = [];

      const selectedId = () => deckRoot
        .querySelector<HTMLElement>('[data-deck-card][aria-selected="true"]')
        ?.dataset.paperId;
      const sample = () => {
        const stage = deckRoot.querySelector<HTMLElement>('.paper-deck__stage');
        const currentSelectedId = selectedId();

        if (stage && currentSelectedId && currentSelectedId !== initialSelectedId) {
          const stageBox = stage.getBoundingClientRect();
          const cards = Array.from(
            deckRoot.querySelectorAll<HTMLElement>('[data-deck-card]'),
            (card): CardGeometry => {
              const box = card.getBoundingClientRect();
              return {
                center: box.left + box.width / 2,
                inlineTransform: card.style.transform,
                layoutOffset: Number(card.dataset.layoutOffset),
                paperId: card.dataset.paperId ?? '',
                selected: card.getAttribute('aria-selected') === 'true',
              };
            },
          ).sort((left, right) => left.layoutOffset - right.layoutOffset);

          frames.push({
            elapsedMs: performance.now() - startedAt,
            stageCenter: stageBox.left + stageBox.width / 2,
            cards,
          });
        }
      };
      const waitForSelectionChange = (previousId: string) => new Promise<string>(
        (resolve, reject) => {
          let remainingFrames = 60;
          const observe = () => {
            const currentId = selectedId();
            if (currentId && currentId !== previousId) {
              resolve(currentId);
              return;
            }
            remainingFrames -= 1;
            if (remainingFrames <= 0) {
              reject(new Error(`Selection did not move away from ${previousId}.`));
              return;
            }
            requestAnimationFrame(observe);
          };
          requestAnimationFrame(observe);
        },
      );
      const move = async (button: HTMLButtonElement, previousId: string) => {
        if (button.disabled) {
          throw new Error('A required deck movement control became disabled.');
        }
        button.click();
        return waitForSelectionChange(previousId);
      };

      let keepSampling = true;
      const sampleFrames = () => {
        sample();
        if (keepSampling) requestAnimationFrame(sampleFrames);
      };
      requestAnimationFrame(sampleFrames);

      const firstForwardId = await move(nextButton, initialSelectedId);
      const secondForwardId = await move(nextButton, firstForwardId);
      const reversedId = await move(previousButton, secondForwardId);

      await new Promise<void>((resolve) => {
        const settleUntil = performance.now() + 400;
        const settle = () => {
          if (performance.now() >= settleUntil) {
            resolve();
            return;
          }
          requestAnimationFrame(settle);
        };
        requestAnimationFrame(settle);
      });

      keepSampling = false;
      sample();
      return {
        frames,
        selectionIds: [
          initialSelectedId,
          firstForwardId,
          secondForwardId,
          reversedId,
        ],
      } satisfies DeckRun;
    });

    expect(run.selectionIds[1]).not.toBe(run.selectionIds[0]);
    expect(run.selectionIds[2]).not.toBe(run.selectionIds[1]);
    expect(run.selectionIds[3]).toBe(run.selectionIds[1]);
    const samples = run.frames;
    expect(samples.length).toBeGreaterThan(5);
    const violations = samples.flatMap((sample) => {
      const selected = sample.cards.find((card) => card.selected);
      const slotOrderIsStable = sample.cards.every((card, index, cards) => (
        index === 0 || card.center - cards[index - 1]!.center > 1
      ));
      const selectedIsCentered = selected != null
        && Math.abs(selected.center - sample.stageCenter) <= 2;
      const cardsUseCssSlots = sample.cards.every(
        (card) => card.inlineTransform === '',
      );

      return slotOrderIsStable && selectedIsCentered && cardsUseCssSlots
        ? []
        : [{
            elapsedMs: Math.round(sample.elapsedMs),
            selectedCenterDelta: selected == null
              ? null
              : Math.round((selected.center - sample.stageCenter) * 10) / 10,
            slots: sample.cards.map((card) => ({
              center: Math.round(card.center),
              offset: card.layoutOffset,
              paperId: card.paperId,
            })),
          }];
    });

    expect(violations).toEqual([]);
  });

  test('fits the complete library ledger without an internal horizontal scroller', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto('/workspace/library');

    const shell = page.locator('.paper-table-shell');
    await expect(shell.locator('table')).toBeVisible();

    const geometry = await shell.evaluate((element) => {
      const shellBox = element.getBoundingClientRect();
      const cells = Array.from(
        element.querySelectorAll<HTMLTableCellElement>('tbody tr:first-child td'),
        (cell) => cell.getBoundingClientRect(),
      );
      return {
        cellsFit: cells.length === 8 && cells.every((cell) => (
          cell.width > 0
          && cell.height > 0
          && cell.left >= shellBox.left - 1
          && cell.right <= shellBox.right + 1
        )),
        clientWidth: element.clientWidth,
        pageClientWidth: document.documentElement.clientWidth,
        pageScrollWidth: document.documentElement.scrollWidth,
        scrollWidth: element.scrollWidth,
      };
    });

    expect(geometry.cellsFit).toBe(true);
    expect(geometry.scrollWidth - geometry.clientWidth).toBeLessThanOrEqual(1);
    expect(geometry.pageScrollWidth - geometry.pageClientWidth).toBeLessThanOrEqual(1);
  });

  test('uses the dashboard width without crossing into the inspector rail', async ({
    page,
  }) => {
    for (const width of [2048, 1280]) {
      await page.setViewportSize({ width, height: 1080 });
      await page.goto('/workspace/dashboard');

      const geometry = await page.locator('.dashboard-route').evaluate((route) => {
        const routeBox = route.getBoundingClientRect();
        const mainBox = document.querySelector<HTMLElement>('#workspace-main')
          ?.getBoundingClientRect();
        const summaryBox = route.querySelector<HTMLElement>('.dashboard-summary')
          ?.getBoundingClientRect();
        const deckBox = route.querySelector<HTMLElement>('.paper-deck')
          ?.getBoundingClientRect();
        return {
          deckRight: deckBox?.right ?? Number.POSITIVE_INFINITY,
          mainRight: mainBox?.right ?? Number.NEGATIVE_INFINITY,
          pageOverflow: document.documentElement.scrollWidth
            - document.documentElement.clientWidth,
          routeRight: routeBox.right,
          summaryRatio: (summaryBox?.width ?? 0) / routeBox.width,
          summaryRight: summaryBox?.right ?? Number.POSITIVE_INFINITY,
        };
      });

      expect(Math.max(0, geometry.pageOverflow)).toBe(0);
      expect(geometry.routeRight).toBeLessThanOrEqual(geometry.mainRight + 1);
      expect(geometry.deckRight).toBeLessThanOrEqual(geometry.mainRight + 1);
      expect(geometry.summaryRight).toBeLessThanOrEqual(geometry.routeRight + 1);
      if (width === 2048) expect(geometry.summaryRatio).toBeGreaterThan(0.5);
    }
  });

  test('constrains the new-review picker without introducing page overflow', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 2048, height: 1080 });
    await page.goto('/workspace/reviews');

    const startPanel = page.locator('.reviews-route__start');
    const picker = startPanel.locator('select');
    await expect(picker).toBeVisible();

    const geometry = await picker.evaluate((element) => {
      const panel = element.closest<HTMLElement>('.reviews-route__start');
      const rootFontSize = Number.parseFloat(
        getComputedStyle(document.documentElement).fontSize,
      );
      return {
        pickerWidth: element.getBoundingClientRect().width,
        maximumPickerWidth: rootFontSize * 32,
        panelOverflow: panel == null ? Number.POSITIVE_INFINITY : panel.scrollWidth - panel.clientWidth,
        pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      };
    });

    expect({
      panelOverflow: Math.max(0, geometry.panelOverflow),
      pageOverflow: Math.max(0, geometry.pageOverflow),
      pickerWithinLimit: geometry.pickerWidth <= geometry.maximumPickerWidth + 1,
    }).toEqual({
      panelOverflow: 0,
      pageOverflow: 0,
      pickerWithinLimit: true,
    });
  });

  test('puts the Reader PDF and workbench in one viewport with matched independent panes', async ({
    page,
  }) => {
    for (const viewport of [
      { width: 2048, height: 1080 },
      { width: 1440, height: 900 },
      { width: 1100, height: 900 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto('/workspace/reader/paper-lifecycle');
      await expect(page.getByRole('article', { name: '第 1 页' })).toHaveAttribute(
        'data-status',
        'ready',
      );

      const workbench = page.getByRole('region', { name: '论文阅读工作台' });
      await expect(workbench.getByRole('tab')).toHaveCount(4);
      await expect(workbench.getByRole('tab', { name: '上下文' })).toHaveAttribute(
        'aria-selected',
        'true',
      );
      await expect(
        workbench.getByRole('button', { name: '批量生成缺失讲解' }),
      ).toHaveCount(0);
      await expect(page.locator('.workspace-page-header')).toHaveCount(0);

      const geometry = await page.locator('.reader-route').evaluate((route) => {
        const box = (selector: string) => route
          .querySelector<HTMLElement>(selector)
          ?.getBoundingClientRect();
        const header = box('.reader-route__header');
        const stage = box('.reader-route__stage');
        const rail = box('.reader-route__rail');
        const pdfPage = box('.pdf-page');
        const pdfViewport = route.querySelector<HTMLElement>('.pdf-workspace__viewport');
        const workbenchContent = route.querySelector<HTMLElement>('.artifact-panel__content');

        return {
          documentOverflow: document.documentElement.scrollHeight
            - document.documentElement.clientHeight,
          headerTop: header?.top ?? Number.POSITIVE_INFINITY,
          pdfPageTop: pdfPage?.top ?? Number.POSITIVE_INFINITY,
          pdfOverflowY: pdfViewport == null
            ? ''
            : getComputedStyle(pdfViewport).overflowY,
          railBottom: rail?.bottom ?? Number.NEGATIVE_INFINITY,
          railTop: rail?.top ?? Number.POSITIVE_INFINITY,
          stageBottom: stage?.bottom ?? Number.POSITIVE_INFINITY,
          stageTop: stage?.top ?? Number.NEGATIVE_INFINITY,
          workbenchOverflowY: workbenchContent == null
            ? ''
            : getComputedStyle(workbenchContent).overflowY,
        };
      });

      expect(Math.max(0, geometry.documentOverflow), `${viewport.width}px outer scroll`).toBe(0);
      expect(geometry.headerTop, `${viewport.width}px compact title top`).toBeLessThan(90);
      expect(geometry.pdfPageTop, `${viewport.width}px PDF starts in first viewport`).toBeLessThan(
        viewport.height,
      );
      expect(Math.abs(geometry.stageTop - geometry.railTop)).toBeLessThanOrEqual(1);
      expect(Math.abs(geometry.stageBottom - geometry.railBottom)).toBeLessThanOrEqual(1);
      expect(geometry.pdfOverflowY).toBe('auto');
      expect(geometry.workbenchOverflowY).toBe('auto');
    }
  });

  test('adapts the Reader toolbar and PDF width to the pane before allowing zoom overflow', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1100, height: 900 });
    await page.goto('/workspace/reader/paper-lifecycle');
    await expect(page.getByRole('article', { name: '第 1 页' })).toHaveAttribute(
      'data-status',
      'ready',
    );

    const toolbarGeometry = await page.locator('.pdf-workspace__toolbar').evaluate((toolbar) => {
      const toolbarBox = toolbar.getBoundingClientRect();
      const children = Array.from(toolbar.children, (child) => child as HTMLElement)
        .filter((child) => getComputedStyle(child).display !== 'none')
        .map((child) => child.getBoundingClientRect());
      const identity = toolbar.querySelector<HTMLElement>('.pdf-workspace__identity')
        ?.getBoundingClientRect();
      const controls = Array.from(
        toolbar.querySelectorAll<HTMLElement>('button, input, output'),
        (control) => control.getBoundingClientRect(),
      );
      const controlsTop = Math.min(
        toolbar.querySelector<HTMLElement>('.pdf-workspace__pagination')
          ?.getBoundingClientRect().top ?? Number.POSITIVE_INFINITY,
        toolbar.querySelector<HTMLElement>('.pdf-workspace__zoom')
          ?.getBoundingClientRect().top ?? Number.POSITIVE_INFINITY,
      );
      return {
        childrenFit: children.every((child) => (
          child.left >= toolbarBox.left - 1 && child.right <= toolbarBox.right + 1
        )) && controls.every((control) => (
          control.left >= toolbarBox.left - 1 && control.right <= toolbarBox.right + 1
        )),
        identityOwnsFirstRow: identity != null && identity.bottom <= controlsTop + 1,
        overflow: toolbar.scrollWidth - toolbar.clientWidth,
      };
    });

    expect(Math.max(0, toolbarGeometry.overflow)).toBeLessThanOrEqual(1);
    expect(toolbarGeometry.childrenFit).toBe(true);
    expect(toolbarGeometry.identityOwnsFirstRow).toBe(true);

    for (const width of [760, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto('/workspace/reader/paper-lifecycle');
      const pdfPage = page.getByRole('article', { name: '第 1 页' });
      await expect(pdfPage).toHaveAttribute('data-status', 'ready');
      const viewport = page.locator('.pdf-workspace__viewport');

      const defaultOverflow = await viewport.evaluate(
        (element) => element.scrollWidth - element.clientWidth,
      );
      expect(Math.max(0, defaultOverflow), `${width}px PDF at 100%`).toBeLessThanOrEqual(1);

      await page.getByRole('button', { name: '放大 PDF' }).click();
      await page.getByRole('button', { name: '放大 PDF' }).click();
      await expect(page.getByRole('status', { name: '当前缩放比例' })).toHaveText('120%');
      await expect(pdfPage).toHaveAttribute('data-status', 'ready');
      await expect.poll(
        () => viewport.evaluate((element) => element.scrollWidth - element.clientWidth),
      ).toBeGreaterThan(1);
    }
  });

  test('contains a long Reader title inside the mobile header', async ({ page }) => {
    const longTitle = '生命周期安全研究阅读器'.repeat(18);
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto('/workspace/reader/paper-lifecycle');
    await page.evaluate(async (titleZh) => {
      const response = await fetch('/api/paper/update', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ id: 'paper-lifecycle', title_zh: titleZh }),
      });
      if (!response.ok) throw new Error('Unable to update the Reader title fixture.');
    }, longTitle);
    await page.reload();

    await expect(page.getByRole('heading', { level: 1, name: longTitle })).toBeVisible();
    const geometry = await page.locator('.reader-route__header').evaluate((header) => {
      const title = header.querySelector<HTMLElement>('.reader-route__title');
      const headerBox = header.getBoundingClientRect();
      const titleBox = title?.getBoundingClientRect();
      return {
        headerWidth: headerBox.width,
        titleMaxWidth: title == null ? 'missing' : getComputedStyle(title).maxWidth,
        pageOverflow: document.documentElement.scrollWidth
          - document.documentElement.clientWidth,
        titleWidth: titleBox?.width ?? Number.POSITIVE_INFINITY,
      };
    });

    expect(Math.max(0, geometry.pageOverflow)).toBe(0);
    expect(geometry.titleMaxWidth).toBe('100%');
    expect(geometry.titleWidth).toBeLessThanOrEqual(geometry.headerWidth + 1);
  });

  test('keeps the Library ledger dense beside its inspector at 1100px', async ({ page }) => {
    await page.setViewportSize({ width: 1100, height: 900 });
    await page.goto('/workspace/library');
    const row = page.locator('.paper-table__row').first();
    await expect(row).toBeVisible();

    const geometry = await row.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        display: style.display,
        height: element.getBoundingClientRect().height,
        trackCount: style.gridTemplateColumns.trim().split(/\s+/).filter(Boolean).length,
      };
    });

    expect(geometry.display).toBe('grid');
    expect(geometry.trackCount).toBe(4);
    expect(geometry.height).toBeLessThan(220);
  });

  test('keeps the repaired routes inside narrow viewports', async ({ page }) => {
    const routes = [
      '/workspace/dashboard',
      '/workspace/library',
      '/workspace/reader/paper-lifecycle',
      '/workspace/reviews',
    ] as const;

    for (const width of [1100, 760, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const route of routes) {
        await page.goto(route);

        const geometry = await page.evaluate(() => {
          const tableShell = document.querySelector<HTMLElement>('.paper-table-shell');
          return {
            pageOverflow: document.documentElement.scrollWidth
              - document.documentElement.clientWidth,
            tableOverflow: tableShell == null
              ? 0
              : tableShell.scrollWidth - tableShell.clientWidth,
          };
        });

        expect(Math.max(0, geometry.pageOverflow), `${width}px ${route}`).toBe(0);
        expect(Math.max(0, geometry.tableOverflow), `${width}px ${route}`).toBe(0);
      }
    }
  });
});
