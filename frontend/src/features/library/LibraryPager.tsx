/* eslint-disable react-refresh/only-export-components -- 分页选项常量与控件同文件维护，供路由/测试复用。 */
/* 文献台账客户端分页控件：页码 / 上一页 / 下一页 / 总数与每页条数。 */

import { Button } from '@cloudflare/kumo';

export const PAGE_SIZE_OPTIONS = [20, 30, 50] as const;

export interface LibraryPagerProps {
  readonly page: number;
  readonly pageCount: number;
  readonly pageSize: number;
  readonly total: number;
  readonly rangeStart: number;
  readonly rangeEnd: number;
  readonly onPageChange: (page: number) => void;
  readonly onPageSizeChange: (size: number) => void;
}

type PageItem = number | 'gap-before' | 'gap-after';

function pageItems(page: number, pageCount: number): PageItem[] {
  if (pageCount <= 7) {
    return Array.from({ length: pageCount }, (_, index) => index + 1);
  }
  const items: PageItem[] = [1];
  const low = Math.max(2, page - 1);
  const high = Math.min(pageCount - 1, page + 1);
  if (low > 2) items.push('gap-before');
  for (let value = low; value <= high; value += 1) items.push(value);
  if (high < pageCount - 1) items.push('gap-after');
  items.push(pageCount);
  return items;
}

export function LibraryPager({
  page,
  pageCount,
  pageSize,
  total,
  rangeStart,
  rangeEnd,
  onPageChange,
  onPageSizeChange,
}: LibraryPagerProps) {
  return (
    <nav className="library-pager" aria-label="文献分页">
      <span className="library-pager__range">
        {total === 0 ? '共 0 篇' : `第 ${rangeStart}–${rangeEnd} 条 · 共 ${total} 篇`}
      </span>
      <div className="library-pager__controls">
        <Button
          type="button"
          variant="ghost"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          上一页
        </Button>
        {pageItems(page, pageCount).map((item) => (
          typeof item === 'number' ? (
            <Button
              key={item}
              type="button"
              variant="ghost"
              className="library-pager__page"
              aria-current={item === page ? 'page' : undefined}
              aria-label={`第 ${item} 页`}
              onClick={() => onPageChange(item)}
            >
              {item}
            </Button>
          ) : (
            <span key={item} className="library-pager__gap" aria-hidden="true">…</span>
          )
        ))}
        <Button
          type="button"
          variant="ghost"
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          下一页
        </Button>
      </div>
      <label className="library-pager__size">
        <span>每页</span>
        {/* 保留原生 select：测试依赖 combobox 角色与 selectOptions 交互，
            Kumo Select 为 popover 实现，不暴露该角色。 */}
        <select
          aria-label="每页条数"
          value={pageSize}
          onChange={(event) => onPageSizeChange(Number(event.currentTarget.value))}
        >
          {PAGE_SIZE_OPTIONS.map((size) => (
            <option key={size} value={size}>{size} 条</option>
          ))}
        </select>
      </label>
    </nav>
  );
}
